import { test, request } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  completePosSale,
  expect,
  loginInBrowser,
  OWNER,
  payPosSaleCash,
  seedAcceptedBatch,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

// Verifies the full open-shift → sale → close-shift → /reports loop and
// the "недостача" badge when actual cash < expected.
test.describe("Shift close → Z-report", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("close shift with short cash → /reports shows красный 'недостача' badge", async ({
    page,
  }) => {
    // Heavy spec: seeds a batch then drives open-shift → sale → close →
    // /reports. Give it headroom over the 60s default under suite load.
    test.setTimeout(120_000);
    // ---- Seed: branch + register + supplier + item + a batch of 5 units ----
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("Z-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Z-Cash"));
    const supplier = await seedSupplier(api, uniqueName("Z-Supp"));
    const item = await seedCatalogItem(api, uniqueName("Z-Med"), "50.00");
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "30.00",
      salePrice: "50.00",
      expiresAt: isoDateInDays(120),
      batchNumber: "Z-A",
    });
    await apiAnon.dispose();
    await api.dispose();

    // ---- Drive the UI: open shift, sell 1 unit cash, close short by 10 ----
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });

    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // Complete a 50 TJS cash sale (draft is created lazily on first add).
    await addPosItemToCart(page, {
      brandName: item.brand_name,
      qty: "1",
      expectedCartItems: 1,
    });
    await payPosSaleCash(page, "50.00");
    await completePosSale(page);

    // Close the shift, declaring 140 cash (expected 150 = 100 + 50 sale).
    await page.getByRole("button", { name: "Закрыть смену" }).click();
    const dialog = page.locator('div[role="dialog"]');
    await dialog.getByLabel("Наличные после пересчёта").fill("140");
    // Closing also auto-downloads the Z-report XLSX; that download is asserted in
    // reports-export.spec.ts — here we only check the close + /reports badge.)
    await dialog.getByRole("button", { name: "Подтвердить закрытие смены" }).click();

    // Shift returns to the open-form state — wait for it to settle.
    await expect(page.getByLabel("Наличные в кассе на начало смены")).toBeVisible({ timeout: 15_000 });

    // ---- /reports: choose the closed shift from readable history ----
    await page.evaluate(() => window.localStorage.removeItem("pos:lastClosedShiftId"));
    await page.goto("/reports");
    await page.getByRole("tab", { name: /^Смены/ }).click();
    const shiftRow = page.locator("tbody tr").filter({ hasText: register.name });
    await expect(shiftRow).toContainText(branch.name);
    await shiftRow.getByRole("button", { name: "Открыть" }).click();

    // Three cards rendered — assert via heading roles so we don't collide
    // with sidebar links or field labels named "Касса".
    await expect(page.getByRole("heading", { name: "Смена", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Касса", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Обороты", exact: true })).toBeVisible();

    // Difference badge surfaces as "недостача" because actual < expected.
    await expect(page.getByText(/недостача/i)).toBeVisible();
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
