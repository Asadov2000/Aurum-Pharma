import { request, test, type APIRequestContext } from "@playwright/test";

import {
  API,
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  currentTotp,
  DEV,
  expect,
  installBrowserSession,
  type TokenPair,
  uniqueName,
} from "./helpers";

interface EnrollmentResult {
  secret: string;
  tokens: TokenPair;
}

async function enrollAccount(api: APIRequestContext, email: string): Promise<EnrollmentResult> {
  clearLoginRateLimit(email);
  const codeResponse = await api.post(`${API}/auth/login/code`, { data: { email } });
  expect(codeResponse.ok()).toBe(true);
  const { dev_code: devCode } = (await codeResponse.json()) as { dev_code: string | null };
  expect(devCode).toBeTruthy();

  const verifyResponse = await api.post(`${API}/auth/login/verify`, {
    data: { email, code: devCode },
  });
  expect(verifyResponse.ok()).toBe(true);
  const challenge = (await verifyResponse.json()) as {
    status: "mfa_enrollment_required";
    challenge_token: string;
  };
  expect(challenge.status).toBe("mfa_enrollment_required");

  const startResponse = await api.post(`${API}/auth/mfa/enroll/start`, {
    data: { challenge_token: challenge.challenge_token },
  });
  expect(startResponse.ok()).toBe(true);
  const { secret } = (await startResponse.json()) as { secret: string };

  const confirmResponse = await api.post(`${API}/auth/mfa/enroll/confirm`, {
    data: {
      challenge_token: challenge.challenge_token,
      code: currentTotp(secret),
    },
  });
  expect(confirmResponse.ok()).toBe(true);
  return {
    secret,
    tokens: {
      ...((await confirmResponse.json()) as TokenPair),
      refresh_cookie: confirmResponse.headers()["set-cookie"],
    },
  };
}

test.describe("Protected ownership transfer", () => {
  test("owner and successor complete the transfer through the real UI", async ({ page }) => {
    const anonymousApi = await request.newContext();
    const developerTokens = await apiLogin(anonymousApi, DEV);
    const developerApi = await apiContext(developerTokens.access_token);

    try {
      const suffix = uniqueName("Ownership").toLowerCase().replaceAll(" ", "-");
      const tenantResponse = await developerApi.post("admin/tenants", {
        data: {
          name: `Аптека ${suffix}`,
          contact_email: `${suffix}@e2e.aurum.tj`,
        },
      });
      expect(tenantResponse.status()).toBe(201);
      const tenant = (await tenantResponse.json()) as { id: string };

      const ownerEmail = `owner-${suffix}@e2e.aurum.tj`;
      const ownerResponse = await developerApi.post(`admin/tenants/${tenant.id}/owner`, {
        data: { email: ownerEmail, full_name: "Первый владелец" },
      });
      expect(ownerResponse.status()).toBe(201);

      const ownerEnrollment = await enrollAccount(anonymousApi, ownerEmail);
      const ownerApi = await apiContext(ownerEnrollment.tokens.access_token);

      try {
        const successorEmail = `successor-${suffix}@e2e.aurum.tj`;
        const successorName = "Новый владелец";
        const memberResponse = await developerApi.post(`admin/tenants/${tenant.id}/members`, {
          data: { email: successorEmail, full_name: successorName },
        });
        expect(memberResponse.status()).toBe(201);
        const successor = (await memberResponse.json()) as { user_id: string };

        const activationResponse = await ownerApi.patch(`users/${successor.user_id}`, {
          data: { status: "active" },
        });
        expect(activationResponse.ok()).toBe(true);

        await installBrowserSession(page, ownerEnrollment.tokens);
        await page.goto("/users");
        const successorRow = page.getByRole("row", { name: new RegExp(successorName) });
        await expect(successorRow).toBeVisible();
        await successorRow
          .getByRole("button", { name: `Действия для ${successorName}` })
          .click();
        await page.getByRole("menuitem", { name: "Передать владение" }).click();

        const requestDialog = page.getByRole("dialog", { name: "Передать владение аптекой?" });
        await expect(requestDialog).toContainText("при следующем входе потребуется настроить MFA");
        const transferCreated = page.waitForResponse(
          (response) =>
            response.url().endsWith("/api/v1/ownership-transfers") &&
            response.request().method() === "POST" &&
            response.status() === 201,
        );
        await requestDialog.getByRole("button", { name: "Отправить запрос" }).click();
        await transferCreated;
        await expect(
          page.getByRole("status").filter({ hasText: "Запрос отправлен сотруднику" }),
        ).toBeVisible();

        const successorEnrollment = await enrollAccount(anonymousApi, successorEmail);
        await installBrowserSession(page, successorEnrollment.tokens);
        await page.goto("/security");

        const transferPanel = page.getByTestId("ownership-transfer-panel");
        await expect(transferPanel).toContainText("От Первый владелец");
        await transferPanel.getByRole("button", { name: "Принять владение" }).click();
        const acceptDialog = page.getByRole("dialog", { name: "Стать владельцем аптеки?" });
        await expect(acceptDialog).toContainText("доступ прежнего владельца будет снят");
        const transferAccepted = page.waitForResponse(
          (response) =>
            response.url().includes("/api/v1/ownership-transfers/") &&
            response.url().endsWith("/accept") &&
            response.request().method() === "POST" &&
            response.status() === 200,
        );
        await acceptDialog.getByRole("button", { name: "Принять владение" }).click();
        await transferAccepted;

        await expect(page).toHaveURL(/\/login(?:\?|$)/);
        const oldOwnerAccess = await ownerApi.get("auth/me");
        expect(oldOwnerAccess.status()).toBe(401);
      } finally {
        await ownerApi.dispose();
      }
    } finally {
      await developerApi.dispose();
      await anonymousApi.dispose();
    }
  });
});
