import { request, test } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  completePosSale,
  expect,
  loginInBrowser,
  OWNER,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

test.describe("Payment reconciliation queue", () => {
  test.beforeEach(() => clearLoginRateLimit(OWNER.email));

  test("lets an owner resolve an uncertain card payment without repeating it", async ({ page }) => {
    test.setTimeout(120_000);
    const anonymousApi = await request.newContext();
    const tokens = await apiLogin(anonymousApi, OWNER);
    const api = await apiContext(tokens.access_token);
    const branch = await seedBranch(api, uniqueName("REC-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("REC-Cash"));
    const supplier = await seedSupplier(api, uniqueName("REC-Supp"));
    const item = await seedCatalogItem(api, uniqueName("REC-Med"), "31.00");
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "2",
      purchasePrice: "20.00",
      salePrice: "31.00",
      expiresAt: isoDateInDays(180),
      batchNumber: "REC-A",
    });

    try {
      await loginInBrowser(page, OWNER);
      await page.goto("/pos");
      await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
      await page.getByLabel("Наличные в кассе на начало смены").fill("100");
      await page.getByRole("button", { name: "Открыть смену" }).click();
      await expect(page.getByText("Смена открыта")).toBeVisible();
      await addPosItemToCart(page, {
        brandName: item.brand_name,
        qty: "1",
        expectedCartItems: 1,
        searchKey: catalogSearchKey(item.brand_name),
      });

      await page.getByRole("button", { name: "Карта", exact: true }).click();
      await page.getByRole("button", { name: "Перейти к оплате картой" }).click();
      await page
        .getByRole("dialog", { name: "Сумма оплаты" })
        .getByRole("button", { name: "ОК" })
        .click();
      await expect(page.getByRole("dialog", { name: "Сверка оплаты картой" })).toBeVisible();

      await page.goto("/payment-reconciliation");
      await expect(page.getByRole("heading", { name: "Сверка", exact: true })).toBeVisible();
      await expect(page.getByText(register.name).first()).toBeVisible();
      await page.getByRole("button", { name: "Принять решение" }).first().click();
      const decision = page.getByRole("dialog", { name: "Решение по оплате" });
      await decision.getByRole("textbox", { name: "Терминал", exact: true }).fill("E2E-TERM-REC");
      await decision
        .getByRole("textbox", { name: "Номер операции/документа", exact: true })
        .fill(`E2E-REC-${Date.now()}`);
      await decision.getByRole("checkbox").check();
      await decision.getByRole("button", { name: "Подтвердить оплату" }).click();

      await expect(page.getByText("Оплата подтверждена").first()).toBeVisible();
      await expect(page.getByText("Ждут завершения чека")).toBeVisible();
      await expect(page.getByRole("button", { name: "Принять решение" })).toHaveCount(0);

      await page.evaluate(() => {
        for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
          const key = window.localStorage.key(index);
          if (key?.startsWith("pos:pendingPaymentAttempt:")) {
            window.localStorage.removeItem(key);
          }
        }
      });

      await page.goto("/pos");
      await expect(page.getByLabel(/^Касса$/)).toHaveValue(register.id);
      await expect(page.getByText("Оплачено 31.00", { exact: false })).toBeVisible({
        timeout: 15_000,
      });
      await completePosSale(page);
      await expect(page.getByText(/оформлен/)).toBeVisible();
    } finally {
      await api.dispose();
      await anonymousApi.dispose();
    }
  });
});

function isoDateInDays(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
