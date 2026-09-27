import { randomUUID } from "node:crypto";

import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import { type CashPayment } from "../src/features/billing/cashTypes";
import { type TenantFinancialAccount } from "../src/features/billing/types";
import {
  apiContext,
  apiLogin,
  API,
  clearLoginRateLimit,
  type Creds,
  DEV,
  expect,
  loginInBrowser,
  OWNER,
  selectTouchDensity,
  uniqueName,
} from "./helpers";

// Public fixture credentials from seed_e2e; the guarded runner creates them only
// in a new disposable database. No staff or grants are recreated by this test.
const ADMIN: Creds = {
  email: "admin@aurum.tj",
  password: "Admin1234",
  totpSecret: "KRUGS4ZANFZSAYJAMNXW2L3ON5XCA5DF",
};

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const width = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(width.document).toBeLessThanOrEqual(width.viewport + 1);
}

test("platform billing workspace remains read-only and usable on desktop and touch", async ({
  page,
}) => {
  clearLoginRateLimit(DEV.email);
  await loginInBrowser(page, DEV);

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1024, height: 768 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/admin/billing");

    await expect(
      page.getByRole("heading", { level: 1, name: "Расчёты Aurum", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Архив прежних счетов" }).click();
    await expect(page.getByText("Только чтение")).toBeVisible();
    await expect(page.getByRole("button", { name: /подтвердить оплату/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /создать счёт/i })).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  }

  await selectTouchDensity(page);
  await page.getByRole("button", { name: "Архив прежних счетов" }).click();

  const search = page.getByRole("searchbox", { name: "Аптека или номер счёта" });
  const searchBounds = await search.boundingBox();
  expect(searchBounds).not.toBeNull();
  expect(searchBounds!.height).toBeGreaterThanOrEqual(44);

  const status = page.getByRole("combobox", { name: "Статус", exact: true });
  const statusBounds = await status.boundingBox();
  expect(statusBounds).not.toBeNull();
  expect(statusBounds!.height).toBeGreaterThanOrEqual(44);
  await expect(page.getByRole("button", { name: /^Ещё условия/ })).toHaveCount(0);
});

test("platform pricing archive exposes no subscription commands", async ({ page }) => {
  clearLoginRateLimit(DEV.email);
  await loginInBrowser(page, DEV);
  await page.goto("/admin/billing");
  await page.getByRole("button", { name: "Архив тарифов" }).click();
  await expect(page.getByRole("heading", { name: "Тарифы и версии цен" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать тариф" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Согласовать" })).toHaveCount(0);
  await expect(page.getByText(/operation_id|row_version|price_version_id/i)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
});

test("platform financial console selects a pharmacy without exposing technical ids", async ({
  page,
}) => {
  clearLoginRateLimit(DEV.email);
  await loginInBrowser(page, DEV);
  await page.goto("/admin/billing");

  await page.getByRole("button", { name: "Клиенты и оплаты" }).click();
  await expect(page.getByRole("heading", { name: "Клиенты и оплаты" })).toBeVisible();
  const tenantList = page.getByRole("list", { name: "Аптеки для расчётов" });
  await expect(tenantList).toBeVisible();
  await tenantList.getByRole("button").first().click();

  await expect(page.getByText("Контроль журнала")).toBeVisible();
  await expect(page.getByText("Сбалансирован")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/[0-9a-f]{8}-[0-9a-f-]{27}/i);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  const search = page.getByRole("searchbox", { name: "Аптека" });
  const searchBounds = await search.boundingBox();
  expect(searchBounds).not.toBeNull();
  expect(searchBounds!.height).toBeGreaterThanOrEqual(40);
});

test("real cash advance requires a second Aurum employee before the tenant receives credit and a document", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const contexts: APIRequestContext[] = [];
  const amount = "125.25";
  const purpose = `Оплата услуг Aurum ${uniqueName("cash-advance")}`;

  try {
    const ownerTokens = await apiLogin(page.request, OWNER);
    const ownerApi = await apiContext(ownerTokens.access_token);
    contexts.push(ownerApi);
    const me = await ownerApi.get("auth/me");
    expect(me.status()).toBe(200);
    const owner = (await me.json()) as { home_tenant_id: string };
    const tenantId = owner.home_tenant_id;
    expect(tenantId).toMatch(/^[0-9a-f-]{36}$/i);

    const before = await readTenantAccount(ownerApi);
    const policyBefore = await ownerApi.get("tenant/access-policy");
    expect(policyBefore.status()).toBe(200);
    const accessBefore = await policyBefore.json();

    const creatorTokens = await loginInBrowser(page, DEV);
    let creatorApi = await apiContext(creatorTokens.access_token);
    contexts.push(creatorApi);
    const tenantResponse = await creatorApi.get(`admin/tenants/${tenantId}`);
    expect(tenantResponse.status()).toBe(200);
    const tenant = (await tenantResponse.json()) as { name: string; status: string };
    await openCashTenant(page, tenant.name);
    await page.getByRole("button", { name: "Зарегистрировать наличные" }).click();
    const registration = page.getByRole("dialog", { name: "Регистрация наличной оплаты" });
    await registration.getByLabel("Сумма, TJS").fill(amount);
    await registration.getByLabel("Назначение оплаты").fill(purpose);
    await expect(registration.getByLabel("Зачисление")).toHaveValue("");
    const createdResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/admin/billing/tenants/${tenantId}/cash-payments` &&
        response.request().method() === "POST",
    );
    await registration.getByRole("button", { name: "Передать на подтверждение" }).click();
    const created = await createdResponse;
    expect(created.status()).toBe(201);
    const pending = (await created.json()) as CashPayment;
    expect(pending.status).toBe("pending_approval");
    expect(pending.target_invoice_id).toBeNull();
    expect(pending.amount).toBe(amount);

    // Browser bootstrap rotates the installed refresh session. Use the exact
    // authenticated request which created this document, together with its
    // current cookies, rather than the token returned before that navigation.
    const creatorAuthorization = await created.request().headerValue("authorization");
    if (!creatorAuthorization?.startsWith("Bearer ")) {
      throw new Error("Authenticated cash request is missing its bearer header");
    }
    creatorApi = await request.newContext({
      baseURL: `${API}/`,
      storageState: await page.context().storageState(),
      extraHTTPHeaders: { Authorization: creatorAuthorization },
    });
    contexts.push(creatorApi);
    const creatorIdentity = await creatorApi.get("auth/me");
    expect(creatorIdentity.status()).toBe(200);
    expect(((await creatorIdentity.json()) as { id: string }).id).toBe(pending.created_by);

    const ownRegistration = page
      .locator("li")
      .filter({ has: page.getByText(purpose, { exact: true }) });
    await expect(
      ownRegistration.getByRole("button", { name: "Подтвердить наличные" }),
    ).toBeDisabled();
    await expect(ownRegistration.getByRole("button", { name: "Открыть документ" })).toHaveCount(0);
    const ownDecision = await creatorApi.post(
      `admin/billing/tenants/${tenantId}/cash-payments/${pending.id}/approve`,
      {
        data: { operation_id: randomUUID(), expected_row_version: pending.row_version },
      },
    );
    expect(ownDecision.status()).toBe(422);
    expect(((await ownDecision.json()) as { error: { code: string } }).error.code).toBe(
      "business_rule_violation",
    );

    const whilePending = await readTenantAccount(ownerApi);
    expect(whilePending.credit_balance).toBe(before.credit_balance);
    expect(whilePending.outstanding_amount).toBe(before.outstanding_amount);
    expect(whilePending.payments).toHaveLength(before.payments.length);
    const pendingTenantList = await ownerApi.get("billing/cash-payments?limit=100&offset=0");
    expect(pendingTenantList.status()).toBe(200);
    expect(
      ((await pendingTenantList.json()) as { items: CashPayment[] }).items.some(
        (item) => item.id === pending.id,
      ),
    ).toBe(false);
    const pendingPolicy = await ownerApi.get("tenant/access-policy");
    expect(pendingPolicy.status()).toBe(200);
    expect(await pendingPolicy.json()).toEqual(accessBefore);

    await loginInBrowser(page, ADMIN);
    await openCashTenant(page, tenant.name);
    const independentRegistration = page
      .locator("li")
      .filter({ has: page.getByText(purpose, { exact: true }) });
    await expect(
      independentRegistration.getByRole("button", { name: "Подтвердить наличные" }),
    ).toBeEnabled();
    await independentRegistration.getByRole("button", { name: "Подтвердить наличные" }).click();
    const confirmation = page.getByRole("dialog", { name: "Подтвердить наличные" });
    await confirmation.getByRole("checkbox").check();
    const approvedResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/v1/admin/billing/tenants/${tenantId}/cash-payments/${pending.id}/approve` &&
        response.request().method() === "POST",
    );
    await confirmation.getByRole("button", { name: "Подтвердить получение" }).click();
    const approved = await approvedResponse;
    expect(approved.status()).toBe(200);
    expect(((await approved.json()) as CashPayment).status).toBe("confirmed");
    await expect(
      page.getByRole("status").filter({ hasText: "Наличные подтверждены" }),
    ).toBeVisible();

    const after = await readTenantAccount(ownerApi);
    expect(after.credit_balance).toBe(addMoney(before.credit_balance, amount));
    expect(after.outstanding_amount).toBe(before.outstanding_amount);
    expect(after.payments).toHaveLength(before.payments.length + 1);
    const cashHistory = after.payments.filter((payment) => payment.payment_method === "cash");
    expect(
      cashHistory.some((payment) => payment.amount === amount && payment.credit_amount === amount),
    ).toBe(true);
    const policyAfter = await ownerApi.get("tenant/access-policy");
    expect(policyAfter.status()).toBe(200);
    expect(await policyAfter.json()).toEqual(accessBefore);
    const tenantAfter = await creatorApi.get(`admin/tenants/${tenantId}`);
    expect(tenantAfter.status()).toBe(200);
    expect(((await tenantAfter.json()) as { status: string }).status).toBe(tenant.status);
    const tenantDecision = await ownerApi.post(
      `admin/billing/tenants/${tenantId}/cash-payments/${pending.id}/approve`,
      {
        data: { operation_id: randomUUID(), expected_row_version: 2 },
      },
    );
    expect(tenantDecision.status()).toBe(403);

    await loginInBrowser(page, OWNER);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/billing");
    await expect(
      page.getByRole("heading", { level: 1, name: "Расчёты с Aurum", exact: true }),
    ).toBeVisible();
    const tenantDocument = page
      .locator("li")
      .filter({ has: page.getByText(purpose, { exact: true }) });
    await tenantDocument.getByRole("button", { name: "Открыть документ" }).click();
    const receipt = page.getByRole("dialog", { name: "Подтверждение приёма наличных" });
    await expect(receipt.getByText(`Документ № ${pending.document_number}`)).toBeVisible();
    await expect(receipt.getByText(purpose, { exact: true })).toBeVisible();
    await expect(receipt.getByRole("button", { name: "Печать / сохранить PDF" })).toBeEnabled();
    await expect(receipt.getByRole("textbox")).toHaveCount(0);
    await expect(
      receipt.getByRole("button", { name: /подтвердить|корректировать|удалить/i }),
    ).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  } finally {
    await Promise.all(contexts.map((context) => context.dispose()));
  }
});

async function openCashTenant(page: Page, tenantName: string): Promise<void> {
  await page.goto("/admin/billing");
  await page.getByRole("button", { name: "Клиенты и оплаты" }).click();
  await page.getByRole("searchbox", { name: "Аптека", exact: true }).fill(tenantName);
  await page
    .getByRole("list", { name: "Аптеки для расчётов" })
    .getByRole("button", { name: new RegExp(tenantName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) })
    .click();
  await expect(page.getByRole("heading", { name: "Наличные оплаты", exact: true })).toBeVisible();
}

async function readTenantAccount(api: APIRequestContext): Promise<TenantFinancialAccount> {
  const response = await api.get("billing/financial-account");
  expect(response.status()).toBe(200);
  return (await response.json()) as TenantFinancialAccount;
}

function addMoney(left: string, right: string): string {
  const cents = (value: string) => {
    const [whole, fraction = ""] = value.split(".");
    return BigInt(whole ?? "0") * 100n + BigInt(fraction.padEnd(2, "0"));
  };
  const total = cents(left) + cents(right);
  return `${total / 100n}.${String(total % 100n).padStart(2, "0")}`;
}
