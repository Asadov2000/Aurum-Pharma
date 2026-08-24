import { test } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER } from "./helpers";

test.describe("Catalog import (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("imports an .xlsx via the wizard and the rows land in the catalog", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    // Open the import wizard (the button also appears in the empty state — take
    // the first match, which is the header action).
    await page
      .getByRole("button", { name: /Импорт из файла/ })
      .first()
      .click();

    // Upload the sample workbook (3 rows); the wizard advances to the job step.
    await page.locator('input[type="file"]').setInputFiles("e2e/fixtures/import-sample.xlsx");

    await page.getByRole("button", { name: /Подготовить превью/ }).click();
    await expect(page.getByText("Корректных")).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: /Запустить импорт/ }).click();

    // Celery processes the upload in the worker; the wizard polls until the job
    // reaches success, at which point the rollback action appears.
    await expect(page.getByRole("button", { name: /Откатить/ })).toBeVisible({
      timeout: 30_000,
    });

    // Close the wizard modal (Escape — there are two "Закрыть" controls).
    await page.keyboard.press("Escape");

    // The imported rows are now searchable in the catalog table.
    await page.getByLabel(/Поиск/).fill("ИмпортXLSX Аспирин");
    await expect(
      page
        .locator('section[aria-label="Позиции каталога"] article')
        .filter({ hasText: "ИмпортXLSX Аспирин" }),
    ).toBeVisible({ timeout: 15_000 });
  });
});
