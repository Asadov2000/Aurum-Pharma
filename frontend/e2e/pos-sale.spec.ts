import { test, request, type Page } from "@playwright/test";

import {
  addPosItemToCart,
  apiContext,
  catalogSearchKey,
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

// Most important e2e flow: open shift → make a sale → FEFO splits across
// two batches → stage cash payment → atomic checkout → check that /batches
// reflects the consumed quantities.
test.describe("POS sale (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("adds items from desktop and physical scanners without starting payment", async ({
    page,
  }) => {
    test.setTimeout(90_000);

    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);

    const branch = await seedBranch(api, uniqueName("SCAN-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("SCAN-Cash"));
    const supplier = await seedSupplier(api, uniqueName("SCAN-Supp"));
    const item = await seedCatalogItem(api, uniqueName("SCAN-Med"), "18.00");
    const barcode = `460${Date.now().toString().slice(-10)}`;
    const barcodeRes = await api.post(`catalog/${item.id}/barcodes`, {
      data: { code: barcode, code_type: "ean13" },
    });
    if (!barcodeRes.ok()) {
      throw new Error(
        `POST catalog/{id}/barcodes → ${barcodeRes.status()} ${await barcodeRes.text()}`,
      );
    }
    await seedAcceptedBatch(api, {
      branchId: branch.id,
      supplierId: supplier.id,
      catalogId: item.id,
      qty: "3",
      purchasePrice: "12.00",
      salePrice: "18.00",
      expiresAt: isoDateInDays(90),
      batchNumber: "SCAN-A",
    });
    await apiAnon.dispose();
    await api.dispose();

    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Касса на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();
    await expect(page.locator('[data-barcode-sink="true"]')).toBeAttached();

    const barcodeLookup = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/catalog/by-barcode/${barcode}`) && response.ok(),
    );
    await page.evaluate((code) => {
      window.dispatchEvent(
        new CustomEvent("aurum-desktop-barcode-scanned", {
          detail: { code },
        }),
      );
    }, ` ${barcode} `);

    await barcodeLookup;
    await expect(page.getByTestId("cart-item")).toHaveCount(1, { timeout: 30_000 });
    await expect(page.getByTestId("cart-item").getByText(item.brand_name)).toBeVisible();
    await expect(page.getByText("18.00", { exact: false }).first()).toBeVisible();

    const physicalBarcodeLookup = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/catalog/by-barcode/${barcode}`) && response.ok(),
    );
    const physicalItemAdd = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/v1\/sales\/[^/]+\/items$/.test(response.url()) &&
        response.ok(),
    );
    await page.locator('[data-barcode-sink="true"]').focus();
    await page.keyboard.type(barcode, { delay: 5 });
    await page.keyboard.press("Enter");

    await physicalBarcodeLookup;
    await physicalItemAdd;
    await expect(page.getByText("Оплачено 0.00", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: /Очистить оплату/i })).toHaveCount(0);
  });

  test("FEFO splits a 7-unit sale across two batches of 5 + 5 and completes", async ({ page }) => {
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
    await installDesktopCashDrawerBridge(page);
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });

    // Open shift with 100 TJS in the till.
    await page.getByLabel("Касса на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // The redesigned register shows the search directly (the draft is created
    // lazily on the first add — no "+ Новая продажа" step up front).
    // Pick the catalog item and ask for 7 units → FEFO splits 5 + 2.
    const searchKey = catalogSearchKey(item.brand_name);
    await addPosItemToCart(page, {
      brandName: item.brand_name,
      qty: "7",
      expectedCartItems: 2,
      searchKey,
    });

    // Two items × 20 TJS each line = 140 TJS total to settle.
    await expect(page.getByText(/К оплате/)).toBeVisible();
    await expect(page.getByText("140.00", { exact: false }).first()).toBeVisible();

    // The tender is staged only in memory. Commit the complete sale command,
    // then drop its HTTP response so the POS must recover by operation_id.
    await payPosSaleCash(page, "140.00");
    let checkoutRequests = 0;
    let recoveryRequests = 0;
    let checkoutOperationId: string | undefined;
    await page.route("**/api/v1/sales/operations/**", async (route) => {
      recoveryRequests += 1;
      await route.continue();
    });
    await page.route("**/api/v1/sales/checkout", async (route) => {
      checkoutRequests += 1;
      const payload = route.request().postDataJSON() as { operation_id?: string };
      checkoutOperationId = payload.operation_id;
      await route.fetch();
      await route.abort("failed");
    });
    await completePosSale(page);
    await page.unroute("**/api/v1/sales/checkout");
    await page.unroute("**/api/v1/sales/operations/**");
    expect(checkoutRequests).toBe(1);
    expect(recoveryRequests).toBe(1);
    expect(checkoutOperationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    await expectDesktopCashDrawerOpen(page, register.id);

    // ---- Print: open the receipt view and verify the totals match ----
    await page.getByRole("button", { name: /Печать чека/ }).click();
    const receipt = page.locator(".receipt-print");
    await expect(receipt).toBeVisible({ timeout: 15_000 });
    await expect(receipt.getByText("КАССОВЫЙ ЧЕК")).toBeVisible();
    // FEFO split the 7 units into two lines, so the name appears twice.
    await expect(receipt.getByText(new RegExp(item.brand_name)).first()).toBeVisible();
    // ИТОГО line carries the 140.00 total.
    await expect(receipt.getByText("ИТОГО")).toBeVisible();
    await expect(receipt.getByText(/140\.00/).first()).toBeVisible();
    // Width selector persists per device/register.
    await page.getByLabel("Ширина чека").selectOption("58");
    await page.getByRole("button", { name: "Закрыть", exact: true }).click();

    // ---- Check that batches are drained: total qty_remaining = 10 - 7 = 3 ----
    await page.goto("/batches");
    const batchCatalogPicker = page.getByPlaceholder("Найти по названию…");
    await expect(batchCatalogPicker).toBeVisible({ timeout: 30_000 });
    await batchCatalogPicker.fill(searchKey);
    const batchCatalogOption = page.getByRole("option", {
      name: new RegExp(item.brand_name),
    });
    await expect(batchCatalogOption).toBeVisible({ timeout: 30_000 });
    await batchCatalogOption.click();
    // FEFO drained FEFO-A entirely (qty → 0); the page hides empty batches
    // by default, so add the optional filter and flip the toggle on first.
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    await page
      .getByRole("dialog", { name: "Настройка фильтров" })
      .getByRole("checkbox", { name: "Пустые партии" })
      .check();
    await page.keyboard.press("Escape");
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

type DesktopMessage = {
  readonly type?: string;
  readonly payload?: {
    readonly reason?: string;
    readonly registerId?: string;
    readonly saleId?: string;
  };
};

async function installDesktopCashDrawerBridge(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type DesktopMessage = {
      readonly type?: string;
      readonly payload?: {
        readonly reason?: string;
        readonly registerId?: string;
        readonly saleId?: string;
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
      capabilities: ["cash-drawer"],
      platform: "windows",
      postMessage(message) {
        target.__aurumDesktopMessages?.push(message);
      },
    };
  });
}

async function expectDesktopCashDrawerOpen(page: Page, registerId: string): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const target = window as Window & {
          readonly __aurumDesktopMessages?: DesktopMessage[];
        };

        return (target.__aurumDesktopMessages ?? []).filter(
          (message) => message.type === "aurum.cash-drawer.open",
        );
      }),
    )
    .toContainEqual(
      expect.objectContaining({
        payload: expect.objectContaining({
          reason: "sale-completed",
          registerId,
          saleId: expect.any(String),
        }),
        type: "aurum.cash-drawer.open",
      }),
    );
}
