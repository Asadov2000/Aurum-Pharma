import { request, test, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedRegister,
  uniqueName,
} from "./helpers";

type PaymentMethod = "cash" | "card" | "qr";

interface PaymentSettingsSnapshot {
  pos_payment_methods: PaymentMethod[];
  pos_mixed_payment_enabled: boolean;
}

async function expectNoHorizontalOverflow(page: Page, workspace: string): Promise<void> {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(
    widths.document,
    `${workspace}: ширина документа ${widths.document}px при viewport ${widths.viewport}px`,
  ).toBeLessThanOrEqual(widths.viewport + 1);
}

test.describe("POS payment settings (owner)", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("applies owner-selected payment methods to the register", async ({ page }) => {
    const apiAnon = await request.newContext();
    const tokens = await apiLogin(apiAnon, OWNER);
    const api = await apiContext(tokens.access_token);
    const originalResponse = await api.get("tenant/settings");
    expect(originalResponse.ok()).toBe(true);
    const original = (await originalResponse.json()) as PaymentSettingsSnapshot;
    const branch = await seedBranch(api, uniqueName("PAY-Branch"));
    const register = await seedRegister(api, branch.id, uniqueName("PAY-Cash"));

    try {
      await loginInBrowser(page, OWNER);
      await page.goto("/settings");
      await expect(
        page.getByRole("heading", { level: 1, name: "Настройки", exact: true }),
      ).toBeVisible();
      for (const viewport of [
        { width: 1600, height: 900 },
        { width: 1024, height: 768 },
        { width: 320, height: 568 },
      ]) {
        await page.setViewportSize(viewport);
        await expectNoHorizontalOverflow(page, `/settings @ ${viewport.width}x${viewport.height}`);
      }

      const cash = page.getByRole("checkbox", { name: "Наличные" });
      const card = page.getByRole("checkbox", { name: "Карта" });
      const qr = page.getByRole("checkbox", { name: "QR-код" });
      const mixed = page.getByRole("checkbox", { name: "Разрешить смешанную оплату" });
      await expect(qr).toBeVisible();

      if (!(await qr.isChecked())) {
        await page.locator('label[for="pos-payment-method-qr"]').click();
      }
      if (await cash.isChecked()) {
        await page.locator('label[for="pos-payment-method-cash"]').click();
      }
      if (await card.isChecked()) {
        await page.locator('label[for="pos-payment-method-card"]').click();
      }
      if (await mixed.isChecked()) {
        await page.locator('label[for="pos-mixed-payment-enabled"]').click();
      }
      await expect(cash).not.toBeChecked();
      await expect(card).not.toBeChecked();
      await expect(qr).toBeChecked();
      await expect(mixed).not.toBeChecked();

      const saveResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/tenant/settings") &&
          response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "Сохранить" }).click();
      const savedResponse = await saveResponse;
      expect(savedResponse.ok()).toBe(true);
      const saved = (await savedResponse.json()) as PaymentSettingsSnapshot;
      expect(saved.pos_payment_methods).toEqual(["qr"]);
      expect(saved.pos_mixed_payment_enabled).toBe(false);
      await expect(page.getByRole("status").getByText("Настройки сохранены.")).toBeVisible();

      const persistedResponse = await api.get("tenant/settings");
      expect(persistedResponse.ok()).toBe(true);
      const persisted = (await persistedResponse.json()) as PaymentSettingsSnapshot;
      expect(persisted.pos_payment_methods).toEqual(["qr"]);
      expect(persisted.pos_mixed_payment_enabled).toBe(false);

      await page.setViewportSize({ width: 1600, height: 900 });
      await page.goto("/pos");
      await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
      await page.getByLabel("Касса на начало смены").fill("50");
      await page.getByRole("button", { name: "Открыть смену" }).click();
      await expect(page.getByText("Смена открыта")).toBeVisible();

      const paymentPanel = page.getByRole("region", { name: "К оплате" });
      await expect(paymentPanel.getByRole("button", { name: "QR-код", exact: true })).toBeVisible();
      await expect(paymentPanel.getByRole("button", { name: "Наличные", exact: true })).toHaveCount(
        0,
      );
      await expect(paymentPanel.getByRole("button", { name: "Карта", exact: true })).toHaveCount(0);
      for (const viewport of [
        { width: 1600, height: 900 },
        { width: 1024, height: 768 },
        { width: 320, height: 568 },
      ]) {
        await page.setViewportSize(viewport);
        await expectNoHorizontalOverflow(page, `/pos @ ${viewport.width}x${viewport.height}`);
      }
    } finally {
      const restoreResponse = await api.patch("tenant/settings", {
        data: {
          pos_payment_methods: original.pos_payment_methods,
          pos_mixed_payment_enabled: original.pos_mixed_payment_enabled,
        },
      });
      expect(restoreResponse.ok()).toBe(true);
      await api.dispose();
      await apiAnon.dispose();
    }
  });
});
