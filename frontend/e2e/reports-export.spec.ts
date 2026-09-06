import { test, request, type Page } from "@playwright/test";

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

// All downloadable exports in one profile file: receipt PDF, Z-report XLSX
// (auto-download on shift close), sales-summary XLSX and stock-on-date XLSX
// (from /reports). One seed + sale + close drives them all.
test.describe("Reports export", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("notifies the desktop host when downloading a sales-summary XLSX", async ({ page }) => {
    await installDesktopFileExportBridge(page);
    await page.route("**/api/v1/reports/sales-summary.xlsx**", async (route) => {
      await route.fulfill({
        body: "xlsx",
        contentType: XLSX_MIME_TYPE,
        status: 200,
      });
    });

    await loginInBrowser(page, OWNER);
    await page.goto("/reports");
    const overview = page.getByRole("region", { name: "Продажи за период" });
    await overview.getByLabel("С", { exact: true }).fill("2026-05-01");
    await overview.getByLabel("По", { exact: true }).fill("2026-05-31");

    const summaryDownload = page.waitForEvent("download");
    await overview.getByRole("button", { name: "Скачать XLSX" }).click();
    expect((await summaryDownload).suggestedFilename()).toBe(
      "sales-summary-2026-05-01_2026-05-31.xlsx",
    );
    await expectDesktopFileExport(page, /^sales-summary-2026-05-01_2026-05-31\.xlsx$/);
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

    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // Sell 1 unit, cash, complete.
    await addPosItemToCart(page, {
      brandName: item.brand_name,
      qty: "1",
      expectedCartItems: 1,
    });
    await payPosSaleCash(page, "50.00");
    await completePosSale(page);

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
    await dialog.getByLabel("Наличные после пересчёта").fill("150");
    const zDownload = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Подтвердить закрытие смены" }).click();
    expect((await zDownload).suggestedFilename()).toMatch(/\.xlsx$/);

    // (3) + (4): the period and stock exports on /reports.
    await page.goto("/reports");
    const zReportDialog = page.getByRole("dialog");
    await expect(zReportDialog).toBeVisible();
    await zReportDialog.getByRole("button", { name: "Закрыть", exact: true }).click();
    await page.getByRole("tab", { name: /^Продажи/ }).click();
    const overview = page.getByRole("region", { name: "Продажи за период" });
    await expect(overview.getByText("Чистая выручка")).toBeVisible();

    const summaryDownload = page.waitForEvent("download");
    await overview.getByRole("button", { name: "Скачать XLSX" }).click();
    expect((await summaryDownload).suggestedFilename()).toMatch(/\.xlsx$/);

    await page.getByRole("tab", { name: /^Товары/ }).click();
    const topProducts = page.getByRole("region", { name: "Товары-лидеры" });
    await topProducts.getByRole("button", { name: /^Фильтры/ }).click();
    const productFilters = page.getByRole("dialog", { name: "Фильтры", exact: true });
    await productFilters.getByLabel("Аптечная точка").selectOption({ label: branch.name });
    await productFilters.getByRole("button", { name: "Готово" }).click();
    await topProducts.getByRole("button", { name: "Показать" }).click();
    await expect(
      topProducts.getByRole("table", { name: "Товары-лидеры за выбранный период" }),
    ).toContainText(item.brand_name);

    await page.getByRole("tab", { name: /^Остатки/ }).click();
    const stockReport = page.getByRole("region", { name: "Остатки и сроки годности" });
    await expect(stockReport.getByText(item.brand_name)).toBeVisible();
    const stockDownload = page.waitForEvent("download");
    await stockReport.getByRole("button", { name: "Скачать в Excel" }).click();
    expect((await stockDownload).suggestedFilename()).toMatch(/\.xlsx$/);
  });
});

const XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

type DesktopMessage = {
  readonly type?: string;
  readonly payload?: {
    readonly fileName?: string;
    readonly mimeType?: string;
    readonly sizeBytes?: number;
  };
};

async function installDesktopFileExportBridge(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type DesktopMessage = {
      readonly type?: string;
      readonly payload?: {
        readonly fileName?: string;
        readonly mimeType?: string;
        readonly sizeBytes?: number;
      };
    };
    type DesktopTarget = Window & {
      __aurumDesktopMessages?: DesktopMessage[];
      aurumDesktop?: {
        readonly appVersion: string;
        readonly capabilities: readonly string[];
        readonly platform: "windows";
        postMessage(message: DesktopMessage): void;
      };
    };

    const target = window as DesktopTarget;
    target.__aurumDesktopMessages = [];
    target.aurumDesktop = {
      appVersion: "0.1.0-e2e",
      capabilities: ["file-export"],
      platform: "windows",
      postMessage(message) {
        target.__aurumDesktopMessages?.push(message);
      },
    };
  });
}

async function expectDesktopFileExport(page: Page, expectedFileName: RegExp): Promise<void> {
  await expect
    .poll(() => getDesktopFileExportMessages(page))
    .toContainEqual(
      expect.objectContaining({
        payload: expect.objectContaining({
          fileName: expect.stringMatching(expectedFileName),
          mimeType: expect.stringContaining(XLSX_MIME_TYPE),
          sizeBytes: expect.any(Number),
        }),
        type: "aurum.file-export.request",
      }),
    );
}

async function getDesktopFileExportMessages(page: Page): Promise<DesktopMessage[]> {
  return page.evaluate(() => {
    const target = window as Window & {
      readonly __aurumDesktopMessages?: DesktopMessage[];
    };

    return (target.__aurumDesktopMessages ?? []).filter(
      (message) => message.type === "aurum.file-export.request",
    );
  });
}

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
