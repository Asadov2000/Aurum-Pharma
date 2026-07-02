import { test, request } from "@playwright/test";

import {
  apiContext,
  catalogSearchKey,
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

// All downloadable exports in one profile file: receipt PDF, Z-report XLSX
// (auto-download on shift close), sales-summary XLSX and stock-on-date XLSX
// (from /reports). One seed + sale + close drives them all.
test.describe("Reports export", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("downloads receipt PDF, Z-report, sales-summary and stock XLSX", async ({ page }) => {
    test.setTimeout(120_000);

    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("RX-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("RX-Cash"));
    const supplier = await seedSupplier(api, uniqueName("RX-Supp"));
    const item = await seedCatalogItem(api, uniqueName("RX-Med"), "50.00");
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "5",
      purchasePrice: "30.00",
      salePrice: "50.00",
      expiresAt: isoDateInDays(120),
      batchNumber: "RX-A",
    });
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });

    await page.getByLabel("Касса на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // Sell 1 unit, cash, complete.
    const picker = page.getByPlaceholder(/Поиск товара/);
    await picker.fill(catalogSearchKey(item.brand_name));
    const opt = page.getByRole("button", { name: new RegExp(item.brand_name) });
    await expect(opt).toBeVisible({ timeout: 15_000 });
    await opt.click();
    await page.getByRole("textbox", { name: "Количество" }).fill("1");
    await page.getByRole("button", { name: "Добавить" }).click();
    await expect(page.getByTestId("cart-item")).toHaveCount(1, { timeout: 30_000 });
    const cashPayment = page.getByRole("button", { name: "Наличные" });
    await expect(cashPayment).toBeEnabled({ timeout: 30_000 });
    await cashPayment.click();
    await expect(page.getByText(/Оплачено 50\.00/)).toBeVisible();
    await page.getByRole("button", { name: /Завершить продажу/ }).click();
    await expect(page.getByText(/оформлен/)).toBeVisible({ timeout: 15_000 });

    // (1) Receipt PDF: open the print view, download the server PDF.
    await page.getByRole("button", { name: /Печать чека/ }).click();
    await expect(page.locator(".receipt-print")).toBeVisible({ timeout: 15_000 });
    const pdfDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: /Скачать PDF/ }).click();
    expect((await pdfDownload).suggestedFilename()).toMatch(/\.pdf$/);
    await page.getByRole("button", { name: "Закрыть", exact: true }).click();

    // (2) Z-report XLSX: auto-downloads when the shift closes.
    await page.getByRole("button", { name: "Закрыть смену" }).click();
    const dialog = page.locator('div[role="dialog"]');
    await dialog.getByLabel("Фактическая касса").fill("150");
    const zDownload = page.waitForEvent("download");
    await dialog.locator("button.h-10", { hasText: "Закрыть" }).click();
    expect((await zDownload).suggestedFilename()).toMatch(/\.xlsx$/);

    // (3) + (4): the period and stock exports on /reports.
    await page.goto("/reports");

    const summaryDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: /Скачать сводный отчёт/ }).click();
    expect((await summaryDownload).suggestedFilename()).toMatch(/\.xlsx$/);

    const stockDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: /Скачать отчёт по остаткам/ }).click();
    expect((await stockDownload).suggestedFilename()).toMatch(/\.xlsx$/);
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
