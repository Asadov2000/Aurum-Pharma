import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER } from "./helpers";

const WORKSPACES = [
  { path: "/", heading: "Главная" },
  { path: "/catalog", heading: "Каталог" },
  { path: "/users", heading: "Сотрудники" },
  { path: "/roles", heading: "Роли" },
  { path: "/pos", heading: "Касса" },
] as const;

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewportWidth + 1);
}

test.describe("Interface layout", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("keeps primary workspaces inside desktop and mobile viewports", async ({ page }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1366, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);

      for (const workspace of WORKSPACES) {
        await page.goto(workspace.path);
        await expect(
          page.getByRole("heading", { level: 1, name: workspace.heading, exact: true }),
        ).toBeVisible();
        await expectNoHorizontalOverflow(page);
      }
    }
  });

  test("keeps the role constructor usable on a narrow screen", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginInBrowser(page, OWNER);
    await page.goto("/roles");

    await page.getByRole("button", { name: "+ Создать роль" }).click();
    const dialog = page.getByRole("dialog", { name: "Создать роль" });
    await expect(dialog).toBeVisible();

    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
    await expect(dialog.getByLabel("Название", { exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("avoids expensive blur effects in operational screens", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");

    const blurredElements = await page.evaluate(
      () =>
        Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
          const style = window.getComputedStyle(element);
          return [style.backdropFilter, style.getPropertyValue("-webkit-backdrop-filter")].some(
            (value) => /\bblur\(/i.test(value),
          );
        }).length,
    );

    expect(blurredElements).toBe(0);
  });
});
