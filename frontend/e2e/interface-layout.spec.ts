import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER } from "./helpers";

const WORKSPACES = [
  { path: "/", heading: "Главная" },
  { path: "/catalog", heading: "Каталог" },
  { path: "/users", heading: "Сотрудники" },
  { path: "/roles", heading: "Роли" },
  { path: "/pos", heading: "Касса" },
  { path: "/billing", heading: "Биллинг" },
] as const;

async function expectNoHorizontalOverflow(page: Page, workspace: string) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(
    overflow.documentWidth,
    `${workspace}: ширина документа ${overflow.documentWidth}px при viewport ${overflow.viewportWidth}px`,
  ).toBeLessThanOrEqual(overflow.viewportWidth + 1);
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
        await expectNoHorizontalOverflow(
          page,
          `${workspace.path} @ ${viewport.width}x${viewport.height}`,
        );
      }
    }
  });

  test("uses a compact desktop rail and a contained mobile drawer", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/pos");

    const desktopNavigation = page.getByRole("navigation", { name: "Основная навигация" });
    const navigationBounds = await desktopNavigation.boundingBox();
    expect(navigationBounds).not.toBeNull();
    expect(navigationBounds!.width).toBeGreaterThanOrEqual(68);
    expect(navigationBounds!.width).toBeLessThanOrEqual(76);
    await expect(page.getByRole("heading", { level: 1, name: "Касса", exact: true })).toBeVisible();

    const expandNavigation = page.getByRole("button", {
      name: "Показать названия разделов",
    });
    await expandNavigation.click();
    const desktopDrawer = page.getByRole("dialog", { name: "Меню приложения" });
    await expect(desktopDrawer).toBeVisible();
    await expect(desktopDrawer.getByText("Aurum Pharma")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(desktopDrawer).toBeHidden();
    await expect(expandNavigation).toBeFocused();

    await page.setViewportSize({ width: 320, height: 568 });
    const shellHeader = page.getByTestId("app-shell-header");
    const headerBounds = await shellHeader.boundingBox();
    expect(headerBounds).not.toBeNull();
    expect(headerBounds!.x).toBeGreaterThanOrEqual(0);
    expect(headerBounds!.x + headerBounds!.width).toBeLessThanOrEqual(320);

    await page.getByRole("button", { name: "Открыть меню" }).click();
    const mobileDrawer = page.getByRole("dialog", { name: "Меню приложения" });
    await expect(mobileDrawer).toBeVisible();
    const drawerBounds = await mobileDrawer.boundingBox();
    expect(drawerBounds).not.toBeNull();
    expect(drawerBounds!.x).toBeGreaterThanOrEqual(0);
    expect(drawerBounds!.x + drawerBounds!.width).toBeLessThanOrEqual(320);
    expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");

    await mobileDrawer.getByRole("link", { name: "Роли" }).click();
    await expect(page).toHaveURL(/\/roles$/);
    await expect(mobileDrawer).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
    await expect(page.getByRole("heading", { level: 1, name: "Роли", exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page, "/roles shell @ 320x568");
  });

  test("keeps the role constructor usable on a narrow screen", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.addInitScript(() => {
      window.localStorage.setItem("ui:density", "touch");
    });
    await loginInBrowser(page, OWNER);
    await page.goto("/roles");

    const createRoleButton = page.getByRole("button", { name: "+ Создать роль" });
    await createRoleButton.click();
    const dialog = page.getByRole("dialog", { name: "Создать роль" });
    await expect(dialog).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");

    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(568);
    await expect(dialog.getByLabel("Название", { exact: true })).toBeVisible();
    const groupSelect = dialog.getByLabel("Раздел функций");
    await expect(groupSelect).toBeVisible();
    expect(
      await groupSelect.evaluate((element) => element.getBoundingClientRect().height),
    ).toBeGreaterThanOrEqual(44);
    const saveBounds = await dialog.getByRole("button", { name: "Создать роль" }).boundingBox();
    expect(saveBounds).not.toBeNull();
    expect(saveBounds!.y).toBeGreaterThanOrEqual(bounds!.y);
    expect(saveBounds!.y + saveBounds!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
    await expectNoHorizontalOverflow(page, "/roles constructor touch @ 320x568");

    await dialog.getByLabel("Название", { exact: true }).fill("Новая роль");
    await page.keyboard.press("Escape");
    const discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
    await expect(discardDialog).toBeVisible();
    await discardDialog.getByRole("button", { name: "Выйти без сохранения" }).click();
    await expect(dialog).toBeHidden();
    await expect(createRoleButton).toBeFocused();
  });

  test("keeps the login form usable at the narrow Windows app width", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto("/login");

    await expect(page.getByRole("heading", { level: 1, name: "Aurum Pharma" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByRole("button", { name: "Получить код" })).toBeVisible();
    await expectNoHorizontalOverflow(page, "/login @ 320x568");
  });

  test("persists touch density and keeps its controls usable on a narrow screen", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");
    await expect(page.getByRole("heading", { level: 1, name: "Каталог" })).toBeVisible();

    await page.getByRole("button", { name: "Вид интерфейса" }).click();
    await page.getByRole("button", { name: "Сенсор" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
    expect(
      await page.getByPlaceholder("например: парацетамол").evaluate((element) => {
        return element.getBoundingClientRect().height;
      }),
    ).toBeGreaterThanOrEqual(44);
    await page.keyboard.press("Escape");
    await expectNoHorizontalOverflow(page, "/catalog touch @ 390x844");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
    expect(await page.evaluate(() => window.localStorage.getItem("ui:density"))).toBe("touch");
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
