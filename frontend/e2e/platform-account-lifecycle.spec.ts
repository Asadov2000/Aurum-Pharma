import { randomUUID } from "node:crypto";

import { test } from "@playwright/test";

import { apiContext, apiLogin, DEV, expect, installBrowserSession, uniqueName } from "./helpers";

test.describe("Platform staff account lifecycle", () => {
  test("rotates an invitation and manages the account without restoring access", async ({
    page,
  }) => {
    const tokens = await apiLogin(page.request, DEV);
    const platformApi = await apiContext(tokens.access_token);
    const email = `${uniqueName("platform-lifecycle")}@aurum.tj`;

    try {
      const invitationResponse = await platformApi.post("admin/platform-accounts/invitations", {
        data: { email, full_name: "E2E Lifecycle Candidate" },
      });
      expect(invitationResponse.status()).toBe(201);
      const invitation = (await invitationResponse.json()) as {
        user_id: string;
        activation_token: string;
      };
      const staleReplay = await platformApi.post(
        `admin/platform-accounts/${invitation.user_id}/block`,
        {
          data: {
            version: 2,
            operation_id: randomUUID(),
            reason_code: "security_incident",
            reason: "A stale request must not change the platform account",
          },
        },
      );
      expect(staleReplay.status()).toBe(409);

      await installBrowserSession(page, tokens);
      await page.goto("/admin/accounts");
      await page.getByRole("searchbox", { name: "Сотрудник" }).fill(email);
      await expect(page.getByText(email)).toBeVisible();

      await page
        .getByRole("button", { name: "Действия с аккаунтом E2E Lifecycle Candidate" })
        .click();
      await page.getByRole("menuitem", { name: "Отправить приглашение повторно" }).click();
      await page.getByLabel("Комментарий").fill("Исходное приглашение не было доставлено");
      await page.getByRole("button", { name: "Создать новую ссылку" }).click();
      const activationUrl = await page.getByLabel("Ссылка активации").inputValue();
      const replacementToken = new URLSearchParams(new URL(activationUrl).hash.slice(1)).get("token");
      expect(replacementToken).toBeTruthy();

      const oldActivation = await platformApi.post("auth/platform-activation", {
        data: { token: invitation.activation_token, password: "PlatformE2E123" },
      });
      expect(oldActivation.status()).toBe(401);
      const activation = await platformApi.post("auth/platform-activation", {
        data: { token: replacementToken, password: "PlatformE2E123" },
      });
      expect(activation.status()).toBe(200);

      await page.getByRole("button", { name: "Готово" }).click();
      await page.reload();
      await page.getByRole("searchbox", { name: "Сотрудник" }).fill(email);
      await expect(candidateRow(page, email).getByText("Активен", { exact: true })).toBeVisible();

      await runAction(
        page,
        email,
        "Заблокировать",
        "Подтверждённый инцидент безопасности аккаунта",
      );
      await expect(
        candidateRow(page, email).getByText("Заблокирован", { exact: true }),
      ).toBeVisible();

      await runAction(page, email, "Разблокировать", "Проверка безопасности аккаунта завершена");
      await expect(candidateRow(page, email).getByText("Активен", { exact: true })).toBeVisible();

      await runAction(page, email, "Вывести из команды", "Работа сотрудника в компании завершена");
      await expect(
        candidateRow(page, email).getByText("Выведен из команды", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Действия с аккаунтом E2E Lifecycle Candidate" }),
      ).toHaveCount(0);
    } finally {
      await platformApi.dispose();
    }
  });
});

async function runAction(
  page: import("@playwright/test").Page,
  email: string,
  action: "Заблокировать" | "Разблокировать" | "Вывести из команды",
  reason: string,
): Promise<void> {
  await page.getByRole("button", { name: "Действия с аккаунтом E2E Lifecycle Candidate" }).click();
  await page.getByRole("menuitem", { name: action }).click();
  await page.getByLabel("Комментарий").fill(reason);
  await page.getByRole("button", { name: action }).click();
}

function candidateRow(page: import("@playwright/test").Page, email: string) {
  return page.getByRole("row").filter({ hasText: email });
}
