import { test } from "@playwright/test";

import { apiContext, apiLogin, DEV, expect, uniqueName } from "./helpers";

test.describe("Platform staff account activation", () => {
  test("consumes a developer invitation once without granting platform access", async ({
    page,
  }) => {
    const tokens = await apiLogin(page.request, DEV);
    const platformApi = await apiContext(tokens.access_token);
    const email = `${uniqueName("platform-e2e")}@aurum.tj`;

    try {
      const invitationResponse = await platformApi.post("admin/platform-accounts/invitations", {
        data: { email, full_name: "E2E Platform Candidate" },
      });
      expect(invitationResponse.status()).toBe(201);
      const invitation = (await invitationResponse.json()) as {
        activation_token: string | null;
        status: string;
      };
      expect(invitation.status).toBe("invited");
      expect(invitation.activation_token).toBeTruthy();

      const token = invitation.activation_token!;
      await page.goto(`/activate-platform?token=${encodeURIComponent(token)}`);
      await expect(page).toHaveURL(/\/activate-platform$/);

      const password = "PlatformE2E123";
      await page.getByLabel("Новый пароль").fill(password);
      await page.getByLabel("Повторите пароль").fill(password);
      await page.getByRole("button", { name: "Активировать аккаунт" }).click();
      await expect(page.getByRole("heading", { name: "Аккаунт активирован" })).toBeVisible();

      const replayResponse = await platformApi.post("auth/platform-activation", {
        data: { token, password: "AnotherPlatform123" },
      });
      expect(replayResponse.status()).toBe(401);
    } finally {
      await platformApi.dispose();
    }
  });
});
