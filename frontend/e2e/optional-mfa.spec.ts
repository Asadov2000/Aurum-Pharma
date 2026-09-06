import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  currentTotp,
  DEV,
  expect,
  loginInBrowser,
  uniqueName,
} from "./helpers";

test("new owner may skip MFA, enable and disable it in settings, and confirm a critical action by password", async ({
  page,
}) => {
  const anonymousApi = await request.newContext();
  const developerTokens = await apiLogin(anonymousApi, DEV);
  const developerApi = await apiContext(developerTokens.access_token);
  try {
    const suffix = uniqueName("optional-mfa").toLowerCase();
    const ownerEmail = `owner-${suffix}@e2e.aurum.tj`;
    const successorEmail = `successor-${suffix}@e2e.aurum.tj`;
    const password = uniqueName("Optional-owner-password");
    const tenantResponse = await developerApi.post("admin/tenants", {
      data: { name: `Аптека ${suffix}`, contact_email: `${suffix}@e2e.aurum.tj` },
    });
    expect(tenantResponse.status()).toBe(201);
    const tenant = (await tenantResponse.json()) as { id: string };
    expect(
      (
        await developerApi.post(`admin/tenants/${tenant.id}/owner`, {
          data: { email: ownerEmail, full_name: "Владелец добровольной защиты" },
        })
      ).status(),
    ).toBe(201);
    expect(
      (
        await developerApi.post(`admin/tenants/${tenant.id}/members`, {
          data: { email: successorEmail, full_name: "Получатель владения" },
        })
      ).status(),
    ).toBe(201);
    await apiLogin(anonymousApi, { email: successorEmail, password: "" });

    await loginInBrowser(page, { email: ownerEmail, password: "" });
    await page.goto("/");
    const suggestion = page.getByRole("region", { name: "Предложение защиты аккаунта" });
    await expect(suggestion).toBeVisible();
    await expect(page.getByRole("link", { name: "Касса", exact: true })).toBeVisible();
    await suggestion.getByRole("button", { name: "Пока пропустить" }).click();
    await expect(suggestion).toBeHidden();
    await page.reload();
    await expect(page.getByRole("link", { name: "Касса", exact: true })).toBeVisible();
    await expect(suggestion).toBeHidden();

    await page.goto("/settings?section=security");
    clearLoginRateLimit(ownerEmail);
    await page.getByRole("button", { name: "Получить код для создания пароля" }).click();
    await expect(page.getByLabel("Код из письма")).not.toHaveValue("");
    await page.getByLabel("Новый пароль", { exact: true }).fill(password);
    await page.getByLabel("Повторите пароль").fill(password);
    await page.getByRole("button", { name: "Сохранить пароль" }).click();
    await page.getByRole("button", { name: "Включить защиту", exact: true }).click();
    await page.getByLabel("Пароль аккаунта").fill(password);
    await page.getByRole("button", { name: "Продолжить настройку" }).click();
    const secret = await page
      .getByText("Секретный ключ", { exact: true })
      .locator("..")
      .locator("code")
      .innerText();
    await page.getByLabel("Я сохранил резервные коды в безопасном месте").check();
    await page.getByLabel("Код из приложения").fill(currentTotp(secret));
    await page.getByRole("button", { name: "Подтвердить и включить" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Двухфакторная защита включена." }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Выключить защиту" }).click();
    await page.getByLabel("Пароль аккаунта").fill(password);
    await expect(page.getByLabel("Код из приложения")).toHaveCount(0);
    await page.getByRole("button", { name: "Подтвердить отключение" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Двухфакторная защита выключена." }),
    ).toBeVisible();

    await loginInBrowser(page, { email: ownerEmail, password });
    await page.goto("/users");
    await expect(suggestion).toBeHidden();
    const successor = page.getByRole("row", { name: /Получатель владения/ });
    await successor.getByRole("button", { name: "Действия для Получатель владения" }).click();
    await page.getByRole("menuitem", { name: "Передать владение" }).click();
    await page
      .getByRole("dialog", { name: "Передать владение аптекой?" })
      .getByRole("button", { name: "Отправить запрос" })
      .click();
    const passwordDialog = page.getByRole("dialog", { name: "Подтверждение действия" });
    await expect(passwordDialog).toBeVisible();
    await passwordDialog.getByLabel("Пароль аккаунта").fill(password);
    await passwordDialog.getByRole("button", { name: "Подтвердить" }).click();
    await expect(passwordDialog).toBeHidden();
    await expect(
      page.getByRole("status").filter({ hasText: "Запрос отправлен сотруднику" }),
    ).toBeVisible();
  } finally {
    await developerApi.dispose();
    await anonymousApi.dispose();
  }
});
