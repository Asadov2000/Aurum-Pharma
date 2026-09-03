import { expect, request, test } from "@playwright/test";

import {
  API,
  clearLoginRateLimit,
  currentTotp,
  DEV,
  installBrowserSession,
  loginInBrowser,
  type TokenPair,
  uniqueName,
} from "./helpers";

// The real client-onboarding path: a developer creates a pharmacy AND its owner
// account in one flow, then the brand-new owner logs in BY CODE (no password —
// they were never given one) and lands on their own workspace. This is the
// chicken-and-egg guarantee: the owner genuinely has rights and can get in.
test.describe("Owner onboarding (dev)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(DEV.email);
  });

  test("dev creates pharmacy + owner; owner logs in by code", async ({ page }) => {
    await loginInBrowser(page, DEV);
    await page.goto("/admin/tenants");

    const name = uniqueName("E2E Onboard");
    const slug = name.toLowerCase().replace(/\s+/g, "-");
    const ownerEmail = `owner-${slug}@e2e.aurum.tj`;

    // ---- create pharmacy + owner via the UI ----
    await page.getByRole("button", { name: /\+ Новая аптека/ }).click();
    await page.getByLabel("Название", { exact: true }).fill(name);
    await page.getByLabel("Контактный email").fill(`${slug}@e2e.aurum.tj`);
    await page.getByLabel("ФИО владельца").fill("Новый Владелец");
    await page.getByLabel("Email владельца").fill(ownerEmail);
    await page.getByRole("button", { name: /Создать аптеку и владельца/ }).click();

    await expect(page.getByText(/Аптека и владелец созданы/)).toBeVisible();
    await expect(page.getByText(ownerEmail)).toBeVisible();

    // ---- the login-code helper shows a code (dev) ----
    await page.getByRole("button", { name: /Получить код входа/ }).click();
    await expect(page.getByText(/Код входа:/)).toBeVisible();

    // ---- log in AS the new owner, BY CODE, with NO password ----
    clearLoginRateLimit(ownerEmail);
    const api = await request.newContext();
    let tokens: TokenPair;
    try {
      const codeRes = await api.post(`${API}/auth/login/code`, {
        data: { email: ownerEmail },
      });
      expect(codeRes.ok()).toBeTruthy();
      const { dev_code } = (await codeRes.json()) as { dev_code: string | null };
      expect(dev_code).toBeTruthy();

      const verifyRes = await api.post(`${API}/auth/login/verify`, {
        data: { email: ownerEmail, code: dev_code }, // no password — owner has none
      });
      expect(verifyRes.ok()).toBeTruthy();
      const challenge = (await verifyRes.json()) as {
        status: "mfa_enrollment_required";
        challenge_token: string;
      };
      expect(challenge.status).toBe("mfa_enrollment_required");

      const enrollmentRes = await api.post(`${API}/auth/mfa/enroll/start`, {
        data: { challenge_token: challenge.challenge_token },
      });
      expect(enrollmentRes.ok()).toBeTruthy();
      const enrollment = (await enrollmentRes.json()) as { secret: string };

      const confirmRes = await api.post(`${API}/auth/mfa/enroll/confirm`, {
        data: {
          challenge_token: challenge.challenge_token,
          code: currentTotp(enrollment.secret),
        },
      });
      expect(confirmRes.ok()).toBeTruthy();
      tokens = {
        ...((await confirmRes.json()) as TokenPair),
        refresh_cookie: confirmRes.headers()["set-cookie"],
      };
    } finally {
      await api.dispose();
    }

    // ---- land on the owner's own workspace ----
    await installBrowserSession(page, tokens);
    await page.goto("/");

    // The «Владелец» role gives the full owner permission set → tenant items
    // appear (POS + role management), and the owner is NOT a support user.
    await expect(page.getByRole("link", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Роли" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Управление", exact: true })).toHaveCount(0);

    // ---- the owner gets a concrete, actionable launch checklist ----
    await page.goto("/onboarding");
    await expect(page.getByRole("heading", { level: 1, name: "Старт" })).toBeVisible();
    await expect(page.getByText("Профиль аптеки")).toBeVisible();
    await expect(page.getByText("Владелец аптеки")).toBeVisible();
    await expect(page.getByRole("link", { name: "Настроить точку" }).first()).toBeVisible();

    await page.getByRole("link", { name: "Настроить точку" }).first().click();
    await expect(page).toHaveURL(/\/branches$/);
    await expect(
      page
        .locator("#main-content")
        .getByRole("heading", { level: 1, name: "Торговые точки", exact: true }),
    ).toBeVisible();
  });
});
