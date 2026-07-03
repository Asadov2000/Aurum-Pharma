import { test } from "@playwright/test";

import { expect, loginInBrowser, OWNER } from "./helpers";

test.describe("Runtime surface", () => {
  test("marks a normal browser session in the app shell", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("server-status-banner")).toHaveCount(0);

    await expect(page.locator("html")).toHaveAttribute(
      "data-runtime-surface",
      "browser",
    );

    const badge = page.getByTestId("runtime-surface-badge");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText("Веб");
    await expect(badge).toHaveAccessibleName("Режим запуска: Открыто в браузере");
    await expect(badge).toHaveAttribute("title", "Открыто в браузере");
    await expect(
      page.locator("main#main-content").getByTestId("runtime-surface-badge"),
    ).toHaveCount(0);
  });

  test("shows the online-only warning when the browser goes offline", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("offline-status-banner")).toHaveCount(0);
    await expect(page.getByTestId("server-status-banner")).toHaveCount(0);

    try {
      await page.context().setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event("offline")));

      const banner = page.getByTestId("offline-status-banner");
      await expect(banner).toBeVisible();
      await expect(banner).toContainText("Касса работает только онлайн");
      await expect(
        page.locator("main#main-content").getByTestId("offline-status-banner"),
      ).toHaveCount(0);
    } finally {
      await page.context().setOffline(false);
    }
  });
});
