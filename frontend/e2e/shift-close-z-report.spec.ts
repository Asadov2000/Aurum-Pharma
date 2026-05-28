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

    await page.getByLabel("Касса на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    // Complete a 50 TJS cash sale (draft is created lazily on first add).
    const picker = page.getByPlaceholder(/Поиск товара/);
    const searchKey = catalogSearchKey(item.brand_name);
    await picker.fill(searchKey);
    const opt = page.getByRole("button", { name: new RegExp(item.brand_name) });
    await expect(opt).toBeVisible({ timeout: 15_000 });
    await opt.click();
    await page.getByRole("textbox", { name: "Количество" }).fill("1");
    await page.getByRole("button", { name: "Добавить" }).click();
    // One-tap cash tile pays the full remaining balance. Wait for it to settle
    // before completing, else "Завершить" reads a stale remaining and errors.
    await page.getByRole("button", { name: "Наличные" }).click();
    await expect(page.getByText(/Оплачено 50\.00/)).toBeVisible();
    await page.getByRole("button", { name: /Завершить продажу/ }).click();
    await expect(page.getByText(/оформлен/)).toBeVisible({ timeout: 15_000 });

    // Close the shift, declaring 140 cash (expected 150 = 100 + 50 sale).
    await page.getByRole("button", { name: "Закрыть смену" }).click();
    const dialog = page.locator('div[role="dialog"]');
    await dialog.getByLabel("Фактическая касса").fill("140");
    // The header has a "✕" with aria-label="Закрыть" too — match the
    // primary submit button by its size class to disambiguate.
    await dialog.locator("button.h-10", { hasText: "Закрыть" }).click();

    // Shift returns to the open-form state — wait for it to settle.
    await expect(page.getByLabel("Касса на начало смены")).toBeVisible({ timeout: 15_000 });

    // ---- /reports: shift_id prefills from localStorage ----
    await page.goto("/reports");
    const shiftIdInput = page.getByLabel(/ID смены/);
    await expect(shiftIdInput).not.toHaveValue("");
    await page.getByRole("button", { name: /Загрузить/ }).click();

    // Three cards rendered — assert via heading roles so we don't collide
    // with sidebar links or field labels named "Касса".
    await expect(page.getByRole("heading", { name: "Смена" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Обороты" })).toBeVisible();

    // Difference badge surfaces as "недостача" because actual < expected.
    await expect(page.getByText(/недостача/i)).toBeVisible();
  });
});

function isoDateInDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
