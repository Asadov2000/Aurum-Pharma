import { test } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER } from "./helpers";

const FILTER_LAYOUT_PREFIX = "aurum:filter-layout:v1:";

test.describe("Configurable filters", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("persists the layout but never persists filter values", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    const dispensingFilter = page.locator('[data-filter-id="dispensing"]');
    const dispensingSelect = dispensingFilter.getByLabel("Тип отпуска", {
      exact: true,
    });
    await expect(dispensingSelect).toBeVisible();

    await dispensingSelect.selectOption("prescription");
    await expect(page.getByRole("button", { name: "Сбросить (1)" })).toBeEnabled();

    await page.getByRole("button", { name: "Убрать фильтр «Тип отпуска»" }).click();
    await expect(dispensingFilter).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Сбросить/ })).toBeDisabled();

    await page.getByRole("button", { name: /^Фильтры/ }).click();
    const settings = page.getByRole("dialog", { name: "Настройка фильтров" });
    await settings.getByRole("checkbox", { name: /^Тип отпуска/ }).check();
    await page.keyboard.press("Escape");

    await expect(dispensingSelect).toHaveValue("");

    const storedPreferences = await page.evaluate((prefix) => {
      return Object.entries(window.localStorage)
        .filter(([key]) => key.startsWith(prefix))
        .map(([key, value]) => ({ key, value }));
    }, FILTER_LAYOUT_PREFIX);

    expect(storedPreferences).toHaveLength(1);
    expect(JSON.parse(storedPreferences[0]!.value)).toEqual(["search", "dispensing"]);
    expect(storedPreferences[0]!.value).not.toContain("prescription");

    await page.reload();
    await expect(dispensingSelect).toBeVisible();
    await expect(dispensingSelect).toHaveValue("");
  });

  test("keeps the settings panel inside a narrow mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    await page.getByRole("button", { name: /^Фильтры/ }).click();
    const settings = page.getByRole("dialog", { name: "Настройка фильтров" });
    await expect(settings).toBeVisible();

    const bounds = await settings.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  });

  test("sends employee search privately and never persists its value", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/users");

    const searchInput = page.getByLabel("Поиск", { exact: true });
    await expect(searchInput).toBeVisible();
    const searchRequest = page.waitForRequest((request) => {
      if (!request.url().endsWith("/api/v1/users/search") || request.method() !== "POST") {
        return false;
      }
      const body = request.postDataJSON() as { q?: string };
      return body.q === "Demo";
    });

    await searchInput.fill("Demo");
    const request = await searchRequest;

    expect(new URL(request.url()).search).toBe("");
    expect(request.postDataJSON()).toMatchObject({ q: "Demo", page: 1, page_size: 25 });

    const storedValues = await page.evaluate((prefix) => {
      return Object.entries(window.localStorage)
        .filter(([key]) => key.startsWith(prefix))
        .map(([, value]) => value);
    }, FILTER_LAYOUT_PREFIX);
    expect(storedValues.join(" ")).not.toContain("Demo");
  });
});
