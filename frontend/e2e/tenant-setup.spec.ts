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

  test("edits a tenant status and the table reflects it", async ({ page }) => {
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, DEV);
    const api = await apiContext(tokens.access_token);
    try {
      const name = uniqueName("E2E Trial");
      const createRes = await api.post("admin/tenants", {
        data: {
          name,
          contact_email: `${name.toLowerCase().replace(/\s+/g, "-")}@e2e.aurum.tj`,
        },
      });
      if (!createRes.ok()) {
        throw new Error(
          `POST admin/tenants → ${createRes.status()} ${await createRes.text()}`,
        );
      }

      await loginInBrowser(page, DEV);
      await page.goto("/admin/tenants");
      const row = page.getByRole("row", { name: new RegExp(name) });
      await expect(row).toBeVisible();
      await selectActionMenuItem(page, `Действия для ${name}`, "Изменить");
      await page.getByLabel("Статус").selectOption("trial");
      await page.getByRole("button", { name: /^Сохранить$/ }).click();

      await expect(page.locator('div[role="dialog"]')).toHaveCount(0);
      await expect(row.getByText("Пробный")).toBeVisible();
    } finally {
      await apiAnon.dispose();
      await api.dispose();
    }
  });

  test("admin billing drawer creates subscription, invoice, payment", async ({ page }) => {
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, DEV);
    const api = await apiContext(tokens.access_token);
    try {
      const name = uniqueName("E2E Billing");
      const createRes = await api.post("admin/tenants", {
        data: {
          name,
          contact_email: `${name.toLowerCase().replace(/\s+/g, "-")}@e2e.aurum.tj`,
        },
      });
      if (!createRes.ok()) {
        throw new Error(
          `POST admin/tenants → ${createRes.status()} ${await createRes.text()}`,
        );
      }

      await loginInBrowser(page, DEV);
      await page.goto("/admin/tenants");
      const row = page.getByRole("row", { name: new RegExp(name) });
      await expect(row).toBeVisible();
      await selectActionMenuItem(page, `Действия для ${name}`, "Биллинг");

      const dialog = page.locator('div[role="dialog"]');
      await expect(dialog.getByText(`Биллинг: ${name}`)).toBeVisible();

      // 1. Subscription
      await dialog.getByLabel("План").selectOption({ index: 1 });
      await dialog.getByLabel("Точек").fill("1");
      await dialog.getByRole("button", { name: /Создать подписку/ }).click();
      await expect(dialog.getByText(/Подписка создана:/)).toBeVisible();

      const subInput = dialog.getByLabel("ID подписки");
      await expect(subInput).not.toHaveValue("");

      // 2. Invoice
      await dialog.getByLabel("Сумма").first().fill("100.00");
      await dialog.getByRole("button", { name: /Создать счёт/ }).click();
      await expect(dialog.getByText(/Счёт создан:/)).toBeVisible();

      // 3. Payment
      const invInput = dialog.getByLabel("ID счёта");
      await expect(invInput).not.toHaveValue("");
      await dialog.getByLabel("Сумма").nth(1).fill("100.00");
      const payBtn = dialog.getByRole("button", { name: /Записать платёж/ });
      // The drawer is taller than the viewport. Scroll the button into the
      // window via JS — Playwright's own scroller targets a non-scrollable
      // ancestor here and bails out as "element outside viewport".
      await payBtn.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await payBtn.click();
      await expect(dialog.getByText(/Платёж записан/)).toBeVisible();
    } finally {
      await apiAnon.dispose();
      await api.dispose();
    }
  });
});
