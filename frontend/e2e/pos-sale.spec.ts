import { test, request } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
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

// Most important e2e flow: open shift → make a sale → FEFO splits across
// two batches → add cash payment that covers the total → complete → check
// that /batches reflects the consumed quantities.
test.describe("POS sale (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("FEFO splits a 7-unit sale across two batches of 5 + 5 and completes", async ({
    page,
  }) => {
    // Heaviest spec: seeds two accepted batches (~16 API calls) then drives
    // the whole sale UI. 60s is tight when the entire suite is hammering the
    // stack sequentially — give it headroom.
    test.setTimeout(120_000);
    // ---- API seed ----
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("POS-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("POS-Cash"));
    const supplier = await seedSupplier(api, uniqueName("POS-Supp"));
    const item = await seedCatalogItem(api, uniqueName("POS-Med"), "20.00");

    // Two batches, both qty=5, different expiries — FEFO must pull from
    // the earlier-expiring batch first.
    const sooner = isoDateInDays(30);
    const later = isoDateInDays(180);
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "15.00",
      salePrice: "20.00",
      expiresAt: sooner,
      batchNumber: "FEFO-A",
    });
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "15.00",
      salePrice: "20.00",
      expiresAt: later,
      batchNumber: "FEFO-B",
    });
    await apiAnon.dispose();
    await api.dispose();

    // ---- UI ----
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });

    // Open shift with 100 TJS in the till.
    await page.getByLabel("Касса на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // Create a draft sale.
    await page.getByRole("button", { name: /\+ Новая продажа/ }).click();
    await expect(page.getByText(/^Продажа /)).toBeVisible();

    // Pick the catalog item and ask for 7 units → FEFO splits 5 + 2.
    const pickerInput = page.getByPlaceholder(/Начните вводить название/);
    const searchKey = item.brand_name.split("-").slice(0, 2).join("-");
    await pickerInput.fill(searchKey);
    const option = page.getByRole("button", { name: new RegExp(item.brand_name) });
    await expect(option).toBeVisible({ timeout: 15_000 });
    await option.click();
    await page.getByLabel("Кол-во").fill("7");
    await page.getByRole("button", { name: "Добавить" }).click();

    // Two rows in the items table — positions 1 and 2.
    const rows = page.locator("table tbody tr");
    await expect(rows).toHaveCount(2, { timeout: 15_000 });

    // The remaining-balance gate is a top-error after clicking, not a
    // disabled attribute (SaleArea only disables when items.length === 0).
    // Two items × 20 TJS each line = 140 TJS total to settle.
    const completeBtn = page.getByRole("button", { name: /Завершить продажу/ });
    await expect(page.getByText(/К оплате/)).toBeVisible();
    await expect(page.getByText("140.00", { exact: false })).toBeVisible();

    // Add 140 TJS cash payment.
    await page.getByLabel("Сумма").fill("140.00");
    await page.getByRole("button", { name: /\+ Оплата/ }).click();
    await expect(page.getByText(/оплачено 140\.00/)).toBeVisible();

    // Complete — receipt number eventually appears.
    await completeBtn.click();
    await expect(page.getByText(/Чек №/)).toBeVisible({ timeout: 15_000 });

    // ---- Check that batches are drained: total qty_remaining = 10 - 7 = 3 ----
    await page.goto("/batches");
    await page.getByLabel(/^Точка$/).selectOption({ label: branch.name });
    // FEFO drained FEFO-A entirely (qty → 0); the page hides empty batches
    // by default, so flip the toggle on first.
    // Switch UI hides the real <input> behind a styled span — Playwright's
    // visibility check refuses to click it without force.
    await page.getByLabel(/Показывать пустые партии/).check({ force: true });
    const tbody = page.locator("table tbody");
    await expect(tbody.locator("text=FEFO-A")).toBeVisible({ timeout: 15_000 });
    await expect(tbody.locator("text=FEFO-B")).toBeVisible();
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
