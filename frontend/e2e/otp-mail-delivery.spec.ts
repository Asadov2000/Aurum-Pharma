import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";

import { expect, request, test } from "@playwright/test";

import {
  assertDockerAvailable,
  dockerExec,
  E2E_POSTGRES_CONTAINER,
  E2E_POSTGRES_DB,
} from "./docker";
import { API, OWNER, apiContext, apiLogin, clearLoginRateLimit, seedBranch } from "./helpers";

// Native HTTP and captured Docker stdin/stdout keep codes, mail and tokens out
// of browser traces, screenshots, videos and assertion diagnostics.
test.use({ trace: "off", screenshot: "off", video: "off" });

const labelFormat =
  '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}';

function isolatedDocker(args: string[], input?: string): string {
  try {
    return execFileSync("docker", args, {
      input,
      encoding: "utf8",
      timeout: 15_000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
  } catch {
    throw new Error("Isolated mail-delivery Docker operation failed");
  }
}

function runningService(
  project: string,
  service: "platform-mailer" | "celery-beat" | "backend",
): string {
  const id = isolatedDocker([
    "ps",
    "--filter",
    `label=com.docker.compose.project=${project}`,
    "--filter",
    `label=com.docker.compose.service=${service}`,
    "--filter",
    "status=running",
    "--format",
    "{{.ID}}",
  ]);
  if (!/^[a-f0-9]{12,64}$/.test(id)) {
    throw new Error(
      "Mail delivery requires one running mailer and beat in the disposable E2E stack",
    );
  }
  if (isolatedDocker(["inspect", "--format", labelFormat, id]) !== `${project}|${service}`) {
    throw new Error("Mail delivery refuses a container outside its disposable E2E project");
  }
  return id;
}

function isolatedMailer(): string {
  assertDockerAvailable();
  const labels = isolatedDocker(["inspect", "--format", labelFormat, E2E_POSTGRES_CONTAINER]);
  const [project, service] = labels.split("|");
  if (!project || !/^aurum-e2e-[a-z0-9-]+$/.test(project) || service !== "postgres") {
    throw new Error("Mail delivery requires the disposable E2E project");
  }
  runningService(project, "celery-beat");
  return runningService(project, "platform-mailer");
}

// Mailpit's read API stays inside the existing mailer network. The test neither
// sends SMTP itself nor calls/publishes the delivery task: actual beat does that.
const mailboxProgram = String.raw`
import json, re, sys, urllib.request
payload = json.load(sys.stdin)
def read(path):
    with urllib.request.urlopen('http://mailpit:8025/api/v1/' + path, timeout=5) as response:
        return json.load(response)
messages = read('messages?limit=200')['messages']
matching = [message for message in messages if any(
    address['Address'].lower() == payload['email'].lower()
    for address in message.get('To', [])
)]
if payload['mode'] == 'baseline':
    print(json.dumps([message['ID'] for message in matching]))
else:
    result = None
    counts = {'matching': len(matching), 'new': 0, 'withText': 0, 'withCode': 0, 'withOutboxId': 0}
    for message in matching:
        if message['ID'] in payload['baseline']:
            continue
        counts['new'] += 1
        details = read('message/' + message['ID'])
        code = re.search(r'(?<!\d)\d{6}(?!\d)', details.get('Text', ''))
        outbox = re.search(r'auth-login-([0-9a-f-]{36})@', details.get('MessageID', ''))
        counts['withText'] += bool(details.get('Text', ''))
        counts['withCode'] += bool(code)
        counts['withOutboxId'] += bool(outbox)
        if code and outbox:
            result = {'code': code.group(0), 'outboxId': outbox.group(1)}
            break
    print(json.dumps(counts if payload['mode'] == 'diagnostics' else result))
`;

interface DeliveredCode {
  code: string;
  outboxId: string;
}

function readMailbox(
  mailer: string,
  email: string,
  mode: "baseline" | "poll" | "diagnostics",
  baseline: string[] = [],
): unknown {
  const output = isolatedDocker(
    ["exec", "-i", mailer, "python", "-c", mailboxProgram],
    JSON.stringify({ mode, baseline, email }),
  );
  try {
    return JSON.parse(output) as unknown;
  } catch {
    throw new Error("Mailpit returned an invalid test response");
  }
}

function deliveredCode(value: unknown): DeliveredCode | null {
  if (!value || typeof value !== "object" || !("code" in value) || !("outboxId" in value))
    return null;
  if (typeof value.code !== "string" || !/^\d{6}$/.test(value.code)) return null;
  if (typeof value.outboxId !== "string" || !/^[0-9a-f-]{36}$/.test(value.outboxId)) return null;
  return { code: value.code, outboxId: value.outboxId };
}

function isolatedSql(statement: string): string {
  return isolatedDocker(
    [
      "exec",
      "-i",
      E2E_POSTGRES_CONTAINER,
      "psql",
      "-U",
      "postgres",
      "-d",
      E2E_POSTGRES_DB,
      "-v",
      "ON_ERROR_STOP=1",
      "-Atq",
    ],
    statement,
  );
}

function issuanceSnapshot(email: string): string {
  // The generated UUID address remains in captured stdin, never diagnostics.
  return isolatedSql(`SELECT json_build_object(
    'database',current_database(),
    'fixtureInvitedNoPassword',(SELECT count(*) FROM app_user WHERE email_lower='${email}' AND status='invited' AND password_hash IS NULL),
    'codes',(SELECT count(*) FROM email_code WHERE email_lower='${email}'),
    'requests',(SELECT count(*) FROM login_attempt WHERE email_lower='${email}' AND outcome='code_requested'),
    'outbox',(SELECT count(*) FROM auth_email_outbox o JOIN email_code c ON c.id=o.email_code_id WHERE c.email_lower='${email}'),
    'revision',(SELECT version_num FROM alembic_version),
    'issuer',(SELECT json_build_object('owner',pg_get_userbyid(p.proowner),'bodyHash',md5(p.prosrc),'hasOutboxInsert',strpos(p.prosrc,'INSERT INTO public.auth_email_outbox')>0)
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='issue_auth_email_code' AND p.pronargs=8)
  );`);
}

const apiDatabaseProgram = String.raw`
import asyncio, json
from sqlalchemy import text
from app.core.db import AppSessionLocal, app_engine
async def inspect():
    try:
        async with AppSessionLocal() as session:
            row = (await session.execute(text("SELECT current_database() AS database, current_user AS role, session_user AS session_role, (SELECT md5(p.prosrc) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='issue_auth_email_code' AND p.pronargs=8) AS issuer_body_hash"))).mappings().one()
            print(json.dumps(dict(row)))
    except Exception as error:
        print(json.dumps({'inspectionError': type(error).__name__}))
    finally:
        await app_engine.dispose()
asyncio.run(inspect())
`;

function apiDatabaseSnapshot(mailer: string): string {
  const [project] = isolatedDocker(["inspect", "--format", labelFormat, mailer]).split("|");
  if (!project) throw new Error("Isolated API project is unavailable");
  const backend = runningService(project, "backend");
  return isolatedDocker(["exec", backend, "python", "-c", apiDatabaseProgram]);
}

async function inviteEmployee(email: string): Promise<void> {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  let owner: Awaited<ReturnType<typeof apiContext>> | undefined;
  try {
    owner = await apiContext((await apiLogin(anonymous, OWNER)).access_token);
    const templatesResponse = await owner.get("templates");
    expect(templatesResponse.ok()).toBe(true);
    const templates = (await templatesResponse.json()) as {
      slug: string;
      permissions: string[];
    }[];
    const cashier = templates.find((template) => template.slug === "cashier");
    if (!cashier) throw new Error("SMTP fixture requires the cashier role template");
    const roleResponse = await owner.post("roles", {
      data: { name: `SMTP ${randomUUID()}`, permissions: cashier.permissions },
    });
    expect(roleResponse.status()).toBe(201);
    const role = (await roleResponse.json()) as { id: string };
    const branch = await seedBranch(owner, `SMTP ${randomUUID()}`);
    const invitation = await owner.post("users/invite", {
      data: {
        operation_id: randomUUID(),
        email,
        full_name: "SMTP bootstrap test",
        role_id: role.id,
        branch_id: branch.id,
      },
    });
    expect(invitation.status()).toBe(201);
  } finally {
    await owner?.dispose();
    await anonymous.dispose();
  }
}

test("delivers a first-login code through outbox, beat, Celery and SMTP before authenticating", async () => {
  const mailer = isolatedMailer();
  const email = `mail-bootstrap-${randomUUID()}@e2e.aurum.tj`;
  // Exercise the actual employee invitation lifecycle. An orphan invited user
  // is intentionally refused email delivery by the database invitation guard.
  await inviteEmployee(email);
  const fixtureCount = isolatedSql(
    `SELECT count(*) FROM app_user WHERE email_lower='${email}' AND status='invited' AND password_hash IS NULL;`,
  );
  expect(fixtureCount, "SMTP fixture must persist as one invited account without a password").toBe(
    "1",
  );
  clearLoginRateLimit(email);
  const snapshot = readMailbox(mailer, email, "baseline");
  if (!Array.isArray(snapshot) || !snapshot.every((id: unknown) => typeof id === "string")) {
    throw new Error("Mailpit baseline is unavailable");
  }
  const baseline = snapshot as string[];
  const requested = await fetch(`${API}/auth/login/code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
    signal: AbortSignal.timeout(15_000),
  });
  expect(requested.status).toBe(202);
  // Deliberately discard the development response; only the SMTP-delivered code is usable here.
  await requested.arrayBuffer();
  const issuance = issuanceSnapshot(email);
  const parsedIssuance = JSON.parse(issuance) as {
    codes?: unknown;
    requests?: unknown;
    outbox?: unknown;
  };
  if (parsedIssuance.codes !== 1 || parsedIssuance.requests !== 1 || parsedIssuance.outbox !== 1) {
    throw new Error(
      `SMTP issuance did not persist the expected code/request/outbox; state=${issuance}; API=${apiDatabaseSnapshot(mailer)}`,
    );
  }
  let delivery: DeliveredCode | null = null;
  try {
    await expect
      .poll(
        () => {
          delivery = deliveredCode(readMailbox(mailer, email, "poll", baseline));
          return delivery !== null;
        },
        { timeout: 30_000, intervals: [500, 1000] },
      )
      .toBe(true);
  } catch {
    // Only aggregate delivery state is reported; never emit addresses, message
    // bodies, code hashes, encrypted payloads, credentials or authentication codes.
    const outbox = isolatedDocker([
      "exec",
      E2E_POSTGRES_CONTAINER,
      "psql",
      "-U",
      "postgres",
      "-d",
      E2E_POSTGRES_DB,
      "-At",
      "-c",
      `SELECT json_build_object('pending',count(*) FILTER(WHERE o.status='pending'),'processing',count(*) FILTER(WHERE o.status='processing'),'sent',count(*) FILTER(WHERE o.status='sent'),'failed',count(*) FILTER(WHERE o.status='failed'),'cancelled',count(*) FILTER(WHERE o.status='cancelled')) FROM auth_email_outbox o JOIN email_code c ON c.id=o.email_code_id WHERE c.email_lower='${email}';`,
    ]);
    const mailbox = readMailbox(mailer, email, "diagnostics", baseline);
    throw new Error(
      `SMTP delivery timed out; outbox counts=${outbox}; mailbox counts=${JSON.stringify(mailbox)}`,
    );
  }
  const received = delivery as DeliveredCode | null;
  if (!received) throw new Error("No SMTP-delivered login code received");

  await expect
    .poll(
      () =>
        dockerExec(E2E_POSTGRES_CONTAINER, [
          "psql",
          "-U",
          "postgres",
          "-d",
          E2E_POSTGRES_DB,
          "-At",
          "-c",
          `SELECT status='sent' AND sent_at IS NOT NULL AND payload_ciphertext IS NULL FROM auth_email_outbox WHERE id='${received.outboxId}'::uuid;`,
        ]).trim(),
      { timeout: 10_000 },
    )
    .toBe("t");

  const verify = () =>
    fetch(`${API}/auth/login/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        code: received.code,
      }),
      signal: AbortSignal.timeout(15_000),
    });
  const verified = await verify();
  expect(verified.status).toBe(200);
  const tokens = (await verified.json()) as { access_token?: unknown };
  expect(typeof tokens.access_token === "string").toBe(true);
  const me = await fetch(`${API}/auth/me`, {
    headers: { Authorization: `Bearer ${String(tokens.access_token)}` },
    signal: AbortSignal.timeout(15_000),
  });
  expect(me.status).toBe(200);
  const user = (await me.json()) as { email?: unknown };
  expect(user.email === email).toBe(true);
  expect((await verify()).status).toBe(401);
});
