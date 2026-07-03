import { test } from "@playwright/test";

import { expect, loginInBrowser, OWNER } from "./helpers";

test.describe("Runtime surface", () => {
  test("marks a normal browser session in the app shell", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });

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
});
