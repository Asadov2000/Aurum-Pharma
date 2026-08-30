import { expect, test } from "@playwright/test";

import { clearLoginRateLimit, loginInBrowser, OWNER, uniqueName } from "./helpers";

test.describe("Trading points and registers", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("owner creates a workstation and deactivates it safely with its point", async ({ page }) => {
    test.setTimeout(120_000);
    const branchName = uniqueName("E2E Point");
    const registerName = uniqueName("E2E Register");

    await loginInBrowser(page, OWNER);
    await page.goto("/branches");
    await page.getByRole("button", { name: "Добавить торговую точку" }).click();
    const branchEditor = page.getByRole("dialog", { name: "Добавить торговую точку" });
    await branchEditor.getByLabel("Название", { exact: true }).fill(branchName);
    await branchEditor.getByLabel("Адрес").fill("Душанбе, тестовая точка");
    await branchEditor.getByRole("button", { name: "Добавить точку" }).click();
    await expect(branchEditor).toHaveCount(0);
    await expect(page.getByRole("row", { name: new RegExp(branchName) })).toBeVisible();

    await page.goto("/registers");
    await page.getByRole("button", { name: "Добавить рабочую кассу" }).click();
    const registerEditor = page.getByRole("dialog", { name: "Добавить рабочую кассу" });
    await registerEditor.getByLabel("Название кассы").fill(registerName);
    await registerEditor.getByLabel("Торговая точка").selectOption({ label: branchName });
    await registerEditor.getByLabel("Предпочтительный формат чека").selectOption("browser");
    await registerEditor.getByRole("button", { name: "Добавить кассу" }).click();
    await expect(registerEditor).toHaveCount(0);
    await expect(page.getByRole("row", { name: new RegExp(registerName) })).toContainText(
      branchName,
    );

    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: registerName });
    await page.getByLabel("Наличные в кассе на начало смены").fill("0");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    await page.goto("/branches");
    const branchRow = page.getByRole("row", { name: new RegExp(branchName) });
    await branchRow.getByRole("button", { name: "Деактивировать" }).click();
    const blockedDialog = page.getByRole("dialog", {
      name: "Деактивировать торговую точку",
    });
    await blockedDialog.getByRole("button", { name: "Деактивировать" }).click();
    await expect(blockedDialog).toContainText(
      "Нельзя деактивировать торговую точку, пока на одной из её касс открыта смена.",
    );
    await blockedDialog.getByRole("button", { name: "Отмена" }).click();

    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: registerName });
    await page.getByRole("button", { name: "Закрыть смену" }).click();
    const closeDialog = page.getByRole("dialog", { name: "Закрытие смены" });
    await closeDialog.getByLabel("Наличные после пересчёта").fill("0");
    await closeDialog.getByRole("button", { name: "Подтвердить закрытие смены" }).click();
    await expect(page.getByLabel("Наличные в кассе на начало смены")).toBeVisible({
      timeout: 15_000,
    });

    await page.goto("/branches");
    await page
      .getByRole("row", { name: new RegExp(branchName) })
      .getByRole("button", {
        name: "Деактивировать",
      })
      .click();
    const confirmDialog = page.getByRole("dialog", {
      name: "Деактивировать торговую точку",
    });
    await confirmDialog.getByRole("button", { name: "Деактивировать" }).click();
    await expect(page.getByRole("row", { name: new RegExp(branchName) })).toHaveCount(0);

    await page.goto("/registers");
    await expect(page.getByRole("row", { name: new RegExp(registerName) })).toHaveCount(0);
  });
});
