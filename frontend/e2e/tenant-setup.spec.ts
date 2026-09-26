import { test, request } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  DEV,
  expect,
  loginInBrowser,
  selectActionMenuItem,
  uniqueName,
} from "./helpers";

test.describe("Tenant setup (dev)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(DEV.email);
  });

  test("creates a pharmacy + owner and the row shows up on /admin/tenants", async ({ page }) => {
    await loginInBrowser(page, DEV);
    await page.goto("/admin/tenants");

    const name = uniqueName("E2E Tenant");
    const email = `${name.toLowerCase().replace(/\s+/g, "-")}@e2e.aurum.tj`;
    const ownerEmail = `owner-${name.toLowerCase().replace(/\s+/g, "-")}@e2e.aurum.tj`;

    await page.getByRole("button", { name: /\+ Новая аптека/ }).click();
    await page.getByLabel("Название", { exact: true }).fill(name);
    await page.getByLabel("Контактный email").fill(email);
    await page.getByLabel("ФИО владельца").fill("Владелец Тест");
    await page.getByLabel("Email владельца").fill(ownerEmail);
    await page.getByRole("button", { name: /Создать аптеку и владельца/ }).click();

    await expect(page.getByText(/Аптека и владелец созданы/)).toBeVisible();
    await page.getByRole("button", { name: /^Готово$/ }).click();

    await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: email, exact: true })).toBeVisible();
  });

  test("saves access restrictions with a preview and archives the tenant", async ({ page }) => {
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, DEV);
    const api = await apiContext(tokens.access_token);
    try {
      const name = uniqueName("E2E Archive");
      const createRes = await api.post("admin/tenants", {
        data: {
          name,
          contact_email: `${name.toLowerCase().replace(/\s+/g, "-")}@e2e.aurum.tj`,
        },
      });
      if (!createRes.ok()) {
        throw new Error(`POST admin/tenants → ${createRes.status()} ${await createRes.text()}`);
      }

      await loginInBrowser(page, DEV);
      await page.goto("/admin/tenants");
      const row = page.getByRole("row", { name: new RegExp(name) });
      await expect(row).toBeVisible();
      await selectActionMenuItem(page, `Действия для ${name}`, "Управление доступом");
      await page.getByLabel("Режим доступа").selectOption("readonly");
      await page.getByLabel("Лимит сотрудников").fill("5");
      await page.getByLabel("Причина изменения").fill("Проверка ручного ограничения");
      await page.getByRole("button", { name: "Проверить последствия" }).click();
      await expect(page.getByText("Рабочие операции доступны только для чтения.")).toBeVisible();
      await page.getByRole("button", { name: "Подтвердить изменение" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await selectActionMenuItem(page, `Действия для ${name}`, "Управление доступом");
      await expect(page.getByLabel("Режим доступа")).toHaveValue("readonly");
      await expect(page.getByLabel("Лимит сотрудников")).toHaveValue("5");
      await expect(page.getByText("Текущая причина: Проверка ручного ограничения")).toBeVisible();
      await page.getByRole("button", { name: "Закрыть", exact: true }).last().click();
      await selectActionMenuItem(page, `Действия для ${name}`, "Изменить");
      await page.getByLabel("Статус").selectOption("archived");
      await page.getByRole("button", { name: /^Сохранить$/ }).click();

      await expect(page.locator('div[role="dialog"]')).toHaveCount(0);
      await expect(row.getByText("Архив")).toBeVisible();
    } finally {
      await apiAnon.dispose();
      await api.dispose();
    }
  });
});
