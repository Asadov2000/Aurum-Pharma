import { expect, type APIRequestContext, type Page, request } from "@playwright/test";
import { createHmac } from "node:crypto";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";

export const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";

function sqlLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

/**
 * Wipe per-minute rate-limit records for a given login email. The backend
 * caps email-code requests at 1/min; tests that hit /auth/login/code in
 * quick succession otherwise stall on 429 retries that overshoot the
 * per-test timeout. It also clears the seeded support user's replay counter
 * because isolated E2E specs intentionally log in several times per 30-second
 * TOTP window. Production replay behavior is covered by backend tests.
 */
export function clearLoginRateLimit(email: string): void {
  // Drop both buckets the backend checks (1/min AND 10/hr). Tests run dozens
  // of logins within minutes — leaving the hour bucket alone hits the cap
  // long before the suite finishes.
  const emailLiteral = sqlLiteral(email);
  const sql =
    `DELETE FROM email_code WHERE email_lower=${emailLiteral}; ` +
    `DELETE FROM login_attempt WHERE email_lower=${emailLiteral} AND outcome IN ('blocked','code_requested'); ` +
    "UPDATE support_mfa SET last_used_counter=NULL " +
    `WHERE user_id=(SELECT id FROM app_user WHERE lower(email)=${emailLiteral});`;
  dockerExec(E2E_POSTGRES_CONTAINER, ["psql", "-U", "postgres", "-d", E2E_POSTGRES_DB, "-c", sql]);
}

export function makeSupportSessionRequireStepUp(email: string): void {
  const emailLiteral = sqlLiteral(email);
  const userIdQuery = `(SELECT id FROM app_user WHERE lower(email)=${emailLiteral})`;
  const sql =
    "UPDATE session SET mfa_verified_at=now()-INTERVAL '11 minutes' " +
    `WHERE user_id=${userIdQuery} AND revoked_at IS NULL; ` +
    `UPDATE support_mfa SET last_used_counter=NULL WHERE user_id=${userIdQuery};`;
  dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-c",
    sql,
  ]);
}
export const TENANT_ID = process.env.E2E_TENANT_ID ?? "";

export interface Creds {
  email: string;
  password: string;
  totpSecret?: string;
}

export const DEV: Creds = {
  email: "dev@aurum.tj",
  password: "Devdev1234",
  totpSecret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
};
export const OWNER: Creds = { email: "owner@aurum.tj", password: "Owner1234" };

export interface TokenPair {
  access_token: string;
  refresh_cookie?: string;
  token_type: string;
  expires_in: number;
}

const REFRESH_COOKIE_NAME = "aurum_refresh_token";
const REFRESH_COOKIE_PATH = "/api/v1/auth";

function decodeBase32(value: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const character of value.replaceAll("=", "").toUpperCase()) {
    const index = alphabet.indexOf(character);
    if (index < 0) throw new Error("Invalid E2E TOTP secret");
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
    bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2));
  }
  return Buffer.from(bytes);
}

export function currentTotp(secret: string): string {
  const counter = BigInt(Math.floor(Date.now() / 1000 / 30));
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(counter);
  const digest = createHmac("sha1", decodeBase32(secret)).update(message).digest();
  const offset = digest[digest.length - 1]! & 0x0f;
  const binary = digest.readUInt32BE(offset) & 0x7fffffff;
  return (binary % 1_000_000).toString().padStart(6, "0");
}

/**
 * Backend rate-limit on the email-code endpoint is 1/min, 10/hr per email.
 * In CI we hit that limit immediately — caller usually wants to retry.
 */
export async function apiLogin(api: APIRequestContext, creds: Creds): Promise<TokenPair> {
  // Pre-flight: clear BOTH rate-limit buckets (1/min and 10/hr) before EACH
  // attempt so a fresh code request always succeeds. With the bucket wiped
  // a 429 should be impossible; the short bounded retry only covers a rare
  // race with a concurrent suite step. No 60s sleeps — those ballooned the
  // suite to ~25min for no benefit once the bucket is cleared.
  let lastErr = "";
  for (let attempt = 0; attempt < 3; attempt++) {
    clearLoginRateLimit(creds.email);
    const codeRes = await api.post(`${API}/auth/login/code`, {
      data: { email: creds.email },
      timeout: 30_000,
    });
    if (codeRes.status() === 429) {
      lastErr = "429 after bucket clear";
      await sleep(2_000);
      continue;
    }
    if (!codeRes.ok()) {
      throw new Error(`/auth/login/code → ${codeRes.status()} ${await codeRes.text()}`);
    }
    const { dev_code } = (await codeRes.json()) as { dev_code: string | null };
    if (!dev_code) {
      throw new Error(
        "dev_code missing from response — backend ENVIRONMENT must equal 'development'",
      );
    }
    const verifyRes = await api.post(`${API}/auth/login/verify`, {
      data: { email: creds.email, code: dev_code, password: creds.password },
      timeout: 30_000,
    });
    if (!verifyRes.ok()) {
      throw new Error(`/auth/login/verify → ${verifyRes.status()} ${await verifyRes.text()}`);
    }
    const body = (await verifyRes.json()) as
      | TokenPair
      | {
          status: "mfa_required";
          challenge_token: string;
        };
    if ("access_token" in body) {
      return { ...body, refresh_cookie: verifyRes.headers()["set-cookie"] };
    }
    if (!creds.totpSecret || body.status !== "mfa_required") {
      throw new Error(`Unexpected support MFA response: ${body.status}`);
    }
    const mfaRes = await api.post(`${API}/auth/mfa/verify`, {
      data: {
        challenge_token: body.challenge_token,
        code: currentTotp(creds.totpSecret),
      },
      timeout: 30_000,
    });
    if (!mfaRes.ok()) {
      throw new Error(`/auth/mfa/verify -> ${mfaRes.status()} ${await mfaRes.text()}`);
    }
    const tokens = (await mfaRes.json()) as TokenPair;
    return { ...tokens, refresh_cookie: mfaRes.headers()["set-cookie"] };
  }
  throw new Error(`apiLogin failed: ${lastErr}`);
}

/**
 * Drop straight into the app as the given user by installing the httpOnly
 * refresh cookie and keeping the access token in browser memory.
 */
export async function loginInBrowser(page: Page, creds: Creds): Promise<TokenPair> {
  const api = await request.newContext();
  try {
    const tokens = await apiLogin(api, creds);
    await installBrowserSession(page, tokens);
    return tokens;
  } finally {
    await api.dispose();
  }
}

export async function installBrowserSession(page: Page, tokens: TokenPair): Promise<void> {
  const value = refreshCookieValue(tokens.refresh_cookie);
  const apiUrl = new URL(API);
  if (page.url() !== "about:blank") {
    await page.goto("about:blank");
  }
  await page.context().clearCookies();
  await page.context().addCookies([
    {
      name: REFRESH_COOKIE_NAME,
      value,
      domain: apiUrl.hostname,
      path: REFRESH_COOKIE_PATH,
      httpOnly: true,
      secure: apiUrl.protocol === "https:",
      sameSite: "Lax",
    },
  ]);
}

function refreshCookieValue(setCookieHeader: string | undefined): string {
  if (!setCookieHeader) {
    throw new Error("Missing refresh Set-Cookie header from /auth/login/verify");
  }
  const escapedName = REFRESH_COOKIE_NAME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|,\\s*)${escapedName}=([^;]+)`).exec(setCookieHeader);
  if (!match?.[1]) {
    throw new Error("Refresh Set-Cookie header does not contain aurum_refresh_token");
  }
  return match[1];
}

/**
 * Builds an APIRequestContext bound to the backend. We pass `baseURL`
 * with a trailing slash and use *relative* paths (no leading slash) so
 * Playwright resolves them correctly — without that combo the URL parser
 * replaces the entire path instead of appending. See Playwright docs.
 */
export async function apiContext(token: string): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: `${API}/`,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}

export function uniqueName(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/**
 * The catalog search ranks by pg_trgm similarity, so a query that shares a
 * long prefix with many seeded rows (e.g. "E2E Med-…") can push the exact
 * target out of the page_size=10 window once dozens of look-alike rows
 * accumulate across runs. Drive CatalogPicker with the unique tail of a
 * `uniqueName()` value ("<ts36>-<rand>") instead — it matches exactly one row.
 */
export function catalogSearchKey(brandName: string): string {
  const parts = brandName.split("-");
  return parts.length >= 2 ? parts.slice(-2).join("-") : brandName;
}

export async function addPosItemToCart(
  page: Page,
  args: {
    brandName: string;
    qty: string;
    expectedCartItems: number;
    searchKey?: string;
  },
): Promise<void> {
  const picker = page.getByPlaceholder(/Поиск товара/);
  await picker.fill(args.searchKey ?? catalogSearchKey(args.brandName));
  const option = page.getByRole("button", {
    name: new RegExp(escapeRegex(args.brandName)),
  });
  await expect(option).toBeVisible({ timeout: 15_000 });
  await option.click();
  await page.getByRole("textbox", { name: "Количество" }).fill(args.qty);
  await page.getByRole("button", { name: "Добавить" }).click();
  await expect(page.getByTestId("cart-item")).toHaveCount(args.expectedCartItems, {
    timeout: 30_000,
  });
}

export async function payPosSaleCash(page: Page, expectedPaidAmount: string): Promise<void> {
  const cashPayment = page.getByRole("button", { name: "Наличные" });
  await expect(cashPayment).toBeEnabled({ timeout: 30_000 });
  await cashPayment.click();
  await expect(
    page.getByText(new RegExp(`Оплачено ${escapeRegex(expectedPaidAmount)}`)),
  ).toBeVisible();
}

export async function completePosSale(page: Page): Promise<void> {
  await page.getByRole("button", { name: /Завершить продажу/ }).click();
  await expect(page.getByText(/оформлен/)).toBeVisible({ timeout: 15_000 });
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ---------------------------------------------------------------------------
// Seed helpers — used by individual specs to create the minimum needed data
// through the API. Always idempotent at the spec level via unique names.
// ---------------------------------------------------------------------------

export interface SeededBranch {
  id: string;
  name: string;
}

export async function seedBranch(api: APIRequestContext, name: string): Promise<SeededBranch> {
  const res = await api.post("branches", {
    data: { name, branch_type: "pharmacy" },
  });
  expectOk(res, "POST /branches");
  return (await res.json()) as SeededBranch;
}

export interface SeededRegister {
  id: string;
  name: string;
  branch_id: string;
}

export async function seedRegister(
  api: APIRequestContext,
  branchId: string,
  name: string,
): Promise<SeededRegister> {
  const res = await api.post("registers", {
    data: { name, branch_id: branchId, printer_type: "browser" },
  });
  expectOk(res, "POST /registers");
  return (await res.json()) as SeededRegister;
}

export interface SeededSupplier {
  id: string;
  name: string;
}

export async function seedSupplier(api: APIRequestContext, name: string): Promise<SeededSupplier> {
  const res = await api.post("suppliers", {
    data: { name },
  });
  expectOk(res, "POST /suppliers");
  return (await res.json()) as SeededSupplier;
}

export interface SeededCatalogItem {
  id: string;
  brand_name: string;
}

export async function seedCatalogItem(
  api: APIRequestContext,
  name: string,
  basePrice = "10.00",
): Promise<SeededCatalogItem> {
  const res = await api.post("catalog", {
    data: {
      brand_name: name,
      dispensing_type: "otc",
      storage_type: "normal",
      base_price: basePrice,
    },
  });
  expectOk(res, "POST /catalog");
  return (await res.json()) as SeededCatalogItem;
}

export interface SeededBatch {
  id: string;
  qty_remaining: string;
  expires_at: string;
}

/** Create an incoming document, add one item, and accept it. Returns the
 *  freshly-created batch. */
export async function seedAcceptedBatch(
  api: APIRequestContext,
  args: {
    branchId: string;
    supplierId: string;
    catalogId: string;
    qty: string;
    purchasePrice: string;
    salePrice: string;
    expiresAt: string; // YYYY-MM-DD
    batchNumber?: string;
  },
): Promise<SeededBatch> {
  const docRes = await api.post("incoming", {
    data: {
      branch_id: args.branchId,
      supplier_id: args.supplierId,
      document_date: new Date().toISOString().slice(0, 10),
      document_number: `E2E-${Date.now()}`,
    },
  });
  expectOk(docRes, "POST /incoming");
  const doc = (await docRes.json()) as { id: string };

  const itemRes = await api.post(`incoming/${doc.id}/items`, {
    data: {
      catalog_id: args.catalogId,
      batch_number: args.batchNumber ?? `B-${Date.now()}`,
      expires_at: args.expiresAt,
      qty: args.qty,
      purchase_price: args.purchasePrice,
      sale_price: args.salePrice,
    },
  });
  expectOk(itemRes, "POST /incoming/{id}/items");

  const acceptRes = await api.post(`incoming/${doc.id}/accept`);
  expectOk(acceptRes, "POST /incoming/{id}/accept");

  const items = (await (await api.get(`incoming/${doc.id}`)).json()) as {
    items: Array<{ created_batch_id: string }>;
  };
  const batchId = items.items[0]?.created_batch_id;
  if (!batchId) throw new Error("Accepted doc did not yield a batch");
  const batchRes = await api.get(`batches/${batchId}`);
  expectOk(batchRes, "GET /batches/{id}");
  return (await batchRes.json()) as SeededBatch;
}

function expectOk(
  res: { status(): number; ok(): boolean; text(): Promise<string> },
  label: string,
): void {
  if (!res.ok()) {
    throw new Error(`${label} → ${res.status()}`);
  }
}

// Re-export expect for convenience.
export { expect };
