import { randomInt } from "node:crypto";
import { expect, request, test } from "@playwright/test";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import { API, DEV, apiContext, apiLogin, clearLoginRateLimit, uniqueName } from "./helpers";

// Login credentials and OTPs must not enter captured browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

function advanceCodeCooldown(contact: string): void {
  clearLoginRateLimit(contact);
  const literal = contact.replaceAll("'", "''");
  // Only timestamps of this test contact in the guarded disposable database.
  // Code expiry, hashes, used state and attempt limits remain intact.
  dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-c",
    `UPDATE auth_identity_code SET created_at=now()-INTERVAL '61 seconds' WHERE contact='${literal}';`,
  ]);
}

test("first login offers a password, phone login preserves the account, and reset revokes old sessions", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const anonymous = await request.newContext();
  const developer = await apiContext((await apiLogin(anonymous, DEV)).access_token);
  const suffix = uniqueName("identity").toLowerCase();
  const email = `${suffix}@e2e.aurum.tj`;
  const phone = `+99290${randomInt(1_000_000, 10_000_000)}`;
  const password = `Initial-${suffix}-password`;
  const nextPassword = `Changed-${suffix}-password`;
  try {
    const created = await developer.post("admin/tenants", {
      data: { name: `Аптека ${suffix}`, contact_email: email },
    });
    expect(created.status()).toBe(201);
    const tenant = (await created.json()) as { id: string };
    const owner = await developer.post(`admin/tenants/${tenant.id}/owner`, {
      data: { email, full_name: "Проверка способов входа" },
    });
    expect(owner.status()).toBe(201);

    await page.goto("/login");
    await page.getByRole("button", { name: "Первый вход без пароля" }).click();
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByRole("button", { name: "Получить код", exact: true }).click();
    await expect(page.getByLabel("Код из письма", { exact: true })).not.toHaveValue("");
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    const prompt = page.getByRole("region", { name: "Предложение создать пароль" });
    await expect(prompt).toBeVisible();
    await prompt.getByRole("button", { name: "Создать пароль", exact: true }).click();
    advanceCodeCooldown(email);
    await prompt.getByRole("button", { name: "Получить код для создания пароля" }).click();
    await expect(prompt.getByLabel("Код из письма")).not.toHaveValue("");
    await prompt.getByLabel("Новый пароль", { exact: true }).fill(password);
    await prompt.getByLabel("Повторите пароль").fill(password);
    await prompt.getByRole("button", { name: "Сохранить пароль", exact: true }).click();
    await expect(prompt).toBeHidden();

    await page.goto("/settings?section=security");
    await page.getByRole("button", { name: "Добавить номер", exact: true }).click();
    await page.getByLabel("Новый номер телефона").fill(phone);
    await page.getByLabel("Текущий пароль", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Отправить SMS на новый номер" }).click();
    await expect(page.getByLabel("Код на новый номер")).not.toHaveValue("");
    await page.getByRole("button", { name: "Подтвердить номер", exact: true }).click();
    await expect(page.getByText("Телефон подтверждён. Теперь можно входить по SMS.")).toBeVisible();
    await page.getByRole("button", { name: "Выйти", exact: true }).click();

    advanceCodeCooldown(phone);
    await page.getByRole("button", { name: "Телефон", exact: true }).click();
    await page.getByLabel("Номер телефона", { exact: true }).fill(phone);
    await page.getByRole("button", { name: "Получить код", exact: true }).click();
    await expect(page.getByLabel("Код из SMS")).not.toHaveValue("");
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto("/settings?section=security");
    await expect(page.getByText(phone, { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText(phone, { exact: true })).toBeVisible();

    // Separate active session verifies server-side revocation on reset.
    const oldTokens = await apiLogin(anonymous, { email, password });
    const oldSession = await apiContext(oldTokens.access_token);
    try {
      const previousMe = await oldSession.get("auth/me");
      expect(previousMe.status()).toBe(200);
      const identity = (await previousMe.json()) as { id: string; home_tenant_id: string };
      expect(identity.home_tenant_id).toBe(tenant.id);
      await page.getByRole("button", { name: "Выйти", exact: true }).click();
      await page.getByRole("button", { name: "Забыли пароль?", exact: true }).click();
      await page.getByLabel("Email", { exact: true }).fill(email);
      advanceCodeCooldown(email);
      await page.getByRole("button", { name: "Получить код восстановления", exact: true }).click();
      await expect(
        page.getByRole("textbox", { name: "Код восстановления", exact: true }),
      ).not.toHaveValue("");
      await page.getByLabel("Новый пароль", { exact: true }).fill(nextPassword);
      await page.getByLabel("Повторите пароль", { exact: true }).fill(nextPassword);
      await page.getByRole("button", { name: "Сохранить пароль", exact: true }).click();
      await expect(page.getByRole("heading", { name: "Пароль обновлён" })).toBeVisible();
      expect((await oldSession.get("auth/me")).status()).toBe(401);
      const rejected = await anonymous.post(`${API}/auth/login/password`, {
        data: { email, password },
      });
      expect(rejected.status()).toBe(401);

      await page.getByRole("button", { name: "Вернуться ко входу", exact: true }).click();
      await page.getByLabel("Email", { exact: true }).fill(email);
      await page.getByLabel("Пароль", { exact: true }).fill(nextPassword);
      await page.getByRole("button", { name: "Войти", exact: true }).click();
      await expect(page).not.toHaveURL(/\/login/);
      await page.goto("/settings?section=security");
      await expect(page.getByText(phone, { exact: true })).toBeVisible();
    } finally {
      await oldSession.dispose();
    }
  } finally {
    await developer.dispose();
    await anonymous.dispose();
  }
});
