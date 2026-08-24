import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, DEV, expect, loginInBrowser, selectTouchDensity } from "./helpers";

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

  const filters = page.getByRole("button", { name: "Фильтры" });
  const filterBounds = await filters.boundingBox();
  expect(filterBounds).not.toBeNull();
  expect(filterBounds!.height).toBeGreaterThanOrEqual(44);
});

test("platform pricing console exposes protected commands without leaking technical identifiers", async ({
  page,
}) => {
  clearLoginRateLimit(DEV.email);
  await loginInBrowser(page, DEV);
  await page.goto("/admin/billing");

  await page.getByRole("button", { name: "Тарифы и цены" }).click();
  await expect(page.getByRole("heading", { name: "Тарифы и версии цен" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать тариф" })).toBeVisible();
  await expect(page.getByText(/operation_id|row_version|price_version_id/i)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "Создать тариф" }).click();
  const dialog = page.getByRole("dialog", { name: "Новый тариф" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Название").fill("X");
  await dialog.getByLabel("Системный код").fill("AB");
  await dialog.getByRole("button", { name: "Создать тариф" }).click();
  await expect(dialog.getByText("Минимум 2 символа")).toBeVisible();
  await expect(dialog.getByText("Латиница: от 3 символов, без пробелов")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await dialog.getByRole("button", { name: "Закрыть" }).click();

  await page.context().setOffline(true);
  await expect(page.getByText(/финансовые команды временно отключены/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать тариф" })).toBeDisabled();
  await page.context().setOffline(false);
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
