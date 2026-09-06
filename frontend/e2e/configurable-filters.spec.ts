import { test } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER } from "./helpers";

const FILTER_LAYOUT_PREFIX = "aurum:filter-layout:v1:";

test.describe("Configurable filters", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("keeps conditions across drawer close without persisting values", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    const dispensingFilter = page.locator('[data-filter-id="dispensing"]');
    const dispensingSelect = dispensingFilter.getByLabel("Условия отпуска", {
      exact: true,
    });
    await expect(dispensingSelect).toHaveCount(0);
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    const panel = page.getByRole("dialog", { name: "Фильтры", exact: true });
    await expect(dispensingSelect).toBeVisible();

    const filteredResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith("/catalog") &&
        url.searchParams.get("dispensing_type") === "prescription"
      );
    });
    await dispensingSelect.selectOption("prescription");
    expect((await filteredResponse).status()).toBe(200);
    await panel.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(panel).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Сбросить фильтр «Условия отпуска»" }),
    ).toBeVisible();
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    await expect(dispensingSelect).toHaveValue("prescription");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: /^Фильтры/ })).toBeFocused();
    await page.getByRole("button", { name: "Сбросить фильтр «Условия отпуска»" }).click();
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    await expect(dispensingSelect).toHaveValue("");
    await dispensingSelect.selectOption("prescription");
    await panel.getByRole("button", { name: "Готово", exact: true }).click();

    const storedPreferences = await page.evaluate((prefix) => {
      return Object.entries(window.localStorage)
        .filter(([key]) => key.startsWith(prefix))
        .map(([key, value]) => ({ key, value }));
    }, FILTER_LAYOUT_PREFIX);

    expect(storedPreferences).toHaveLength(0);

    await page.reload();
    await page.getByRole("button", { name: /^Фильтры/ }).click();
    await expect(dispensingSelect).toBeVisible();
    await expect(dispensingSelect).toHaveValue("");
  });

  test("keeps the mobile panel scrollable and restores page scrolling on close", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");

    await page.getByRole("button", { name: /^Фильтры/ }).click();
    const settings = page.getByRole("dialog", { name: "Фильтры", exact: true });
    await expect(settings).toBeVisible();

    const bounds = await settings.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
    await expect(page.locator("body")).toHaveCSS("overflow", "hidden");
    const lastField = settings.locator("input, select").last();
    await lastField.scrollIntoViewIfNeeded();
    await expect(lastField).toBeInViewport();
    await settings.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(settings).toHaveCount(0);
    await expect(page.locator("body")).not.toHaveCSS("overflow", "hidden");
    await expect(page.getByRole("button", { name: /^Фильтры/ })).toBeFocused();
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
