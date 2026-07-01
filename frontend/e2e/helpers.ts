import { execSync } from "node:child_process";

import {
  expect,
  type APIRequestContext,
  type Page,
  request,
} from "@playwright/test";

export const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";

/**
 * Wipe per-minute rate-limit records for a given login email. The backend
 * caps email-code requests at 1/min; tests that hit /auth/login/code in
 * quick succession otherwise stall on 429 retries that overshoot the
 * per-test timeout. Safe to use freely in tests — only affects test users.
 */
export function clearLoginRateLimit(email: string): void {
  // Drop both buckets the backend checks (1/min AND 10/hr). Tests run dozens
  // of logins within minutes — leaving the hour bucket alone hits the cap
  // long before the suite finishes.
  const sql =
    `DELETE FROM email_code WHERE email_lower='${email}'; ` +
    `DELETE FROM login_attempt WHERE email_lower='${email}' AND outcome IN ('blocked','code_requested');`;
  execSync(`docker exec aurum-postgres psql -U postgres -d aurum -c "${sql}"`, {
    encoding: "utf8",
  });
}
export const TENANT_ID = process.env.E2E_TENANT_ID ?? "";

export interface Creds {
  email: string;
  password: string;
}

export const DEV: Creds = { email: "dev@aurum.tj", password: "Devdev1234" };
export const OWNER: Creds = { email: "owner@aurum.tj", password: "Owner1234" };

interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

/**
 * Backend rate-limit on the email-code endpoint is 1/min, 10/hr per email.
 * In CI we hit that limit immediately — caller usually wants to retry.
 */
export async function apiLogin(
  api: APIRequestContext,
  creds: Creds,
): Promise<TokenPair> {
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
    });
    if (!verifyRes.ok()) {
      throw new Error(`/auth/login/verify → ${verifyRes.status()} ${await verifyRes.text()}`);
    }
    return (await verifyRes.json()) as TokenPair;
  }
  throw new Error(`apiLogin failed: ${lastErr}`);
}

/**
 * Drop straight into the app as the given user by injecting tokens directly
 * into localStorage — much faster than driving the login form and dodges the
 * email-code rate limit on every spec.
 */
export async function loginInBrowser(
  page: Page,
  creds: Creds,
): Promise<TokenPair> {
  const api = await request.newContext();
  try {
    const tokens = await apiLogin(api, creds);
    // Must navigate to an origin first or localStorage is unreachable.
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      window.localStorage.setItem("aurum.access_token", t.access_token);
      window.localStorage.setItem("aurum.refresh_token", t.refresh_token);
    }, tokens);
    return tokens;
  } finally {
    await api.dispose();
  }
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

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
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

export async function seedSupplier(
  api: APIRequestContext,
  name: string,
): Promise<SeededSupplier> {
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

function expectOk(res: { status(): number; ok(): boolean; text(): Promise<string> }, label: string): void {
  if (!res.ok()) {
    throw new Error(`${label} → ${res.status()}`);
  }
}

// Re-export expect for convenience.
export { expect };
