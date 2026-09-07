import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import {
  assertDockerAvailable,
  dockerExec,
  E2E_POSTGRES_CONTAINER,
  E2E_POSTGRES_DB,
} from "./docker";
import { API, CASHIER, clearLoginRateLimit } from "./helpers";

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

function runningService(project: string, service: "platform-mailer" | "celery-beat"): string {
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
    for message in matching:
        if message['ID'] in payload['baseline']:
            continue
        details = read('message/' + message['ID'])
        code = re.search(r'(?<!\d)\d{6}(?!\d)', details.get('Text', ''))
        outbox = re.search(r'auth-login-([0-9a-f-]{36})@', details.get('MessageID', ''))
        if code and outbox:
            result = {'code': code.group(0), 'outboxId': outbox.group(1)}
            break
    print(json.dumps(result))
`;

interface DeliveredCode {
  code: string;
  outboxId: string;
}

function readMailbox(mailer: string, mode: "baseline" | "poll", baseline: string[] = []): unknown {
  const output = isolatedDocker(
    ["exec", "-i", mailer, "python", "-c", mailboxProgram],
    JSON.stringify({ mode, baseline, email: CASHIER.email }),
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

test("delivers a login code through outbox, beat, Celery and SMTP before authenticating", async () => {
  const mailer = isolatedMailer();
  clearLoginRateLimit(CASHIER.email);
  const snapshot = readMailbox(mailer, "baseline");
  if (!Array.isArray(snapshot) || !snapshot.every((id: unknown) => typeof id === "string")) {
    throw new Error("Mailpit baseline is unavailable");
  }
  const baseline = snapshot as string[];
  const requested = await fetch(`${API}/auth/login/code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: CASHIER.email }),
    signal: AbortSignal.timeout(15_000),
  });
  expect(requested.status).toBe(202);
  // Deliberately discard the development response; only the SMTP-delivered code is usable here.
  await requested.arrayBuffer();
  let delivery: DeliveredCode | null = null;
  await expect
    .poll(
      () => {
        delivery = deliveredCode(readMailbox(mailer, "poll", baseline));
        return delivery !== null;
      },
      { timeout: 30_000, intervals: [500, 1000] },
    )
    .toBe(true);
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
        email: CASHIER.email,
        code: received.code,
        password: CASHIER.password,
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
  expect(user.email === CASHIER.email).toBe(true);
  expect((await verify()).status).toBe(401);
});
