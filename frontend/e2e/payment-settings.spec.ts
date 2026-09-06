import { request, test, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
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
  version: number;
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
      await expect(page.getByTestId("settings-page")).toBeVisible();
      for (const viewport of [
        { width: 1600, height: 900 },
        { width: 1024, height: 768 },
        { width: 320, height: 568 },
      ]) {
        await page.setViewportSize(viewport);
        await expectNoHorizontalOverflow(page, `/settings @ ${viewport.width}x${viewport.height}`);
      }
      await page.setViewportSize({ width: 1600, height: 900 });
      await page.getByRole("button", { name: /Оплата и возвраты/ }).click();

      const cash = page.getByRole("button", { name: "Наличные", exact: true });
      const card = page.getByRole("button", { name: "Карта", exact: true });
      const qr = page.getByRole("button", { name: "QR-код", exact: true });
      const mixed = page.getByRole("checkbox", { name: "Разрешить" });
      await expect(qr).toBeVisible();

      if ((await qr.getAttribute("aria-pressed")) !== "true") await qr.click();
      if ((await cash.getAttribute("aria-pressed")) === "true") await cash.click();
      if ((await card.getAttribute("aria-pressed")) === "true") await card.click();
      if (await mixed.isChecked()) {
        await mixed.focus();
        await mixed.press("Space");
      }
      await expect(cash).toHaveAttribute("aria-pressed", "false");
      await expect(card).toHaveAttribute("aria-pressed", "false");
      await expect(qr).toHaveAttribute("aria-pressed", "true");
      await expect(mixed).not.toBeChecked();

      const saveResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/tenant/settings") &&
          response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "Сохранить изменения" }).click();
      const savedResponse = await saveResponse;
      expect(savedResponse.ok()).toBe(true);
      expect(Object.keys(savedResponse.request().postDataJSON() as object).sort()).toEqual([
        "expected_version",
        "pos_mixed_payment_enabled",
        "pos_payment_methods",
        "refund_reason_mode",
      ]);
      const saved = (await savedResponse.json()) as PaymentSettingsSnapshot;
      expect(saved.pos_payment_methods).toEqual(["qr"]);
      expect(saved.pos_mixed_payment_enabled).toBe(false);
      await expect(page.getByText("Правила оплаты и возвратов сохранены.")).toBeVisible();

      const persistedResponse = await api.get("tenant/settings");
      expect(persistedResponse.ok()).toBe(true);
      const persisted = (await persistedResponse.json()) as PaymentSettingsSnapshot;
      expect(persisted.pos_payment_methods).toEqual(["qr"]);
      expect(persisted.pos_mixed_payment_enabled).toBe(false);

      await page.setViewportSize({ width: 1600, height: 900 });
      await page.goto("/pos");
      await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
      await page.getByLabel("Наличные в кассе на начало смены").fill("50");
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
      const currentResponse = await api.get("tenant/settings");
      expect(currentResponse.ok()).toBe(true);
      const current = (await currentResponse.json()) as PaymentSettingsSnapshot;
      const restoreResponse = await api.patch("tenant/settings", {
        data: {
          expected_version: current.version,
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
