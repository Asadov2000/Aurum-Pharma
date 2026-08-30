import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER, selectTouchDensity } from "./helpers";

const WORKSPACES = [
  { path: "/", heading: "Главная" },
  { path: "/catalog", heading: "Каталог" },
  { path: "/sales", heading: "Чеки и возвраты" },
  { path: "/users", heading: "Сотрудники" },
  { path: "/roles", heading: "Роли" },
  { path: "/pos", heading: "Касса" },
  { path: "/billing", heading: "Тариф и оплата" },
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
      { width: 1024, height: 768 },
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

  test("keeps the launch workspace clear across desktop and mobile", async ({ page }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/onboarding");
      await expect(
        page.getByRole("heading", { level: 1, name: "Старт", exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("heading", { name: "Готовность системы" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Пробный период" })).toBeVisible();
      await expectNoHorizontalOverflow(page, `/onboarding @ ${viewport.width}x${viewport.height}`);
    }
  });

  test("keeps the supplier workspace usable across desktop and mobile", async ({ page }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/suppliers");
      await expect(
        page.getByRole("heading", { level: 1, name: "Поставщики", exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("region", { name: "Сводка по поставщикам" })).toBeVisible();
      await expectNoHorizontalOverflow(page, `/suppliers @ ${viewport.width}x${viewport.height}`);
    }
  });

  test("keeps the incoming workspace usable across desktop and mobile", async ({ page }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/incoming");
      await expect(
        page.getByRole("heading", { level: 1, name: "Приёмка товаров", exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("region", { name: "Сводка по приходам" })).toBeVisible();
      await expectNoHorizontalOverflow(page, `/incoming @ ${viewport.width}x${viewport.height}`);
    }
  });

  test("uses a customizable desktop sidebar and a contained mobile drawer", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/pos");

    const desktopNavigation = page.getByRole("navigation", { name: "Основная навигация" });
    const navigationBounds = await desktopNavigation.boundingBox();
    expect(navigationBounds).not.toBeNull();
    expect(navigationBounds!.width).toBeGreaterThanOrEqual(236);
    expect(navigationBounds!.width).toBeLessThanOrEqual(244);
    await expect(desktopNavigation.getByRole("link", { name: "Касса" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Касса", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Свернуть боковую панель" }).click();
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "compact");
    const compactBounds = await desktopNavigation.boundingBox();
    expect(compactBounds).not.toBeNull();
    expect(compactBounds!.width).toBeGreaterThanOrEqual(68);
    expect(compactBounds!.width).toBeLessThanOrEqual(76);

    await page.getByRole("button", { name: "Развернуть боковую панель" }).click();
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "expanded");

    await page.getByRole("button", { name: "Настроить боковую панель" }).click();
    const settingsDialog = page.getByRole("dialog", { name: "Настроить меню" });
    await expect(settingsDialog).toBeVisible();
    await expect(
      settingsDialog.getByRole("checkbox", { name: "Скрыть раздел «Касса»" }),
    ).toBeDisabled();
    await settingsDialog.getByRole("button", { name: "Добавить «Каталог» в избранное" }).click();
    await settingsDialog.getByRole("button", { name: "Готово" }).click();
    await expect(settingsDialog).toBeHidden();
    await expect(desktopNavigation.getByText("Избранное")).toBeVisible();

    await page.getByRole("button", { name: "Настроить боковую панель" }).click();
    await page
      .getByRole("dialog", { name: "Настроить меню" })
      .getByRole("button", { name: "Авто" })
      .click();
    await page
      .getByRole("dialog", { name: "Настроить меню" })
      .getByRole("button", { name: "Готово" })
      .click();
    await page.setViewportSize({ width: 1100, height: 800 });
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "compact");
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "expanded");

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

    await mobileDrawer.getByRole("button", { name: "Настроить боковую панель" }).click();
    const mobileSettings = page.getByRole("dialog", { name: "Настроить меню" });
    await expect(mobileSettings).toBeVisible();
    const readyBounds = await mobileSettings.getByRole("button", { name: "Готово" }).boundingBox();
    expect(readyBounds).not.toBeNull();
    expect(readyBounds!.y + readyBounds!.height).toBeLessThanOrEqual(568);
    await expectNoHorizontalOverflow(page, "sidebar settings @ 320x568");
    await mobileSettings.getByRole("button", { name: "Готово" }).click();

    await page.getByRole("button", { name: "Открыть меню" }).click();
    const reopenedMobileDrawer = page.getByRole("dialog", { name: "Меню приложения" });
    await expect(reopenedMobileDrawer).toBeVisible();

    await reopenedMobileDrawer.getByRole("link", { name: "Роли" }).click();
    await expect(page).toHaveURL(/\/roles$/);
    await expect(reopenedMobileDrawer).toBeHidden();
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
    await selectTouchDensity(page);

    const createRoleButton = page.locator("header").getByRole("button", { name: "Создать роль" });
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
    await dialog.getByRole("button", { name: /Права доступа/ }).click();
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

    await dialog.getByRole("button", { name: "Создать роль" }).click();
    await expect(dialog.getByRole("button", { name: "О роли", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const roleName = dialog.getByLabel("Название", { exact: true });
    await expect(roleName).toBeFocused();
    await expect(dialog.getByText("Введите название роли")).toBeVisible();
    await roleName.fill("Новая роль");
    await page.keyboard.press("Escape");
    const discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
    await expect(discardDialog).toBeVisible();
    await discardDialog.getByRole("button", { name: "Выйти без сохранения" }).click();
    await expect(dialog).toBeHidden();
    await expect(createRoleButton).toBeFocused();
  });

  test("keeps the employee directory and profile actions usable with touch", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.addInitScript(() => {
      window.localStorage.setItem("ui:density", "touch");
    });
    await loginInBrowser(page, OWNER);
    await page.goto("/users");
    await selectTouchDensity(page);

    const directory = page.getByRole("table", { name: "Сотрудники аптеки" });
    await expect(directory).toBeVisible();
    expect(
      await directory.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
    ).toBe(true);

    const ownerRow = directory.getByRole("row", { name: /Demo Owner/ });
    const actionButton = ownerRow.getByRole("button", { name: "Действия для Demo Owner" });
    const actionBounds = await actionButton.boundingBox();
    expect(actionBounds).not.toBeNull();
    expect(actionBounds!.width).toBeGreaterThanOrEqual(44);
    expect(actionBounds!.height).toBeGreaterThanOrEqual(44);

    await actionButton.click();
    await page.getByRole("menuitem", { name: "Профиль" }).click();
    const profileDialog = page.getByRole("dialog", { name: "Профиль: Demo Owner" });
    await expect(profileDialog).toBeVisible();
    const dialogBounds = await profileDialog.boundingBox();
    expect(dialogBounds).not.toBeNull();
    expect(dialogBounds!.x).toBeGreaterThanOrEqual(0);
    expect(dialogBounds!.x + dialogBounds!.width).toBeLessThanOrEqual(320);
    await page.keyboard.press("Escape");
    await expect(profileDialog).toBeHidden();
    await expect(actionButton).toBeFocused();
    await expectNoHorizontalOverflow(page, "/users touch @ 320x568");

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(directory.getByRole("columnheader", { name: "Сотрудник" })).toBeVisible();
    await expect(directory.getByRole("columnheader", { name: "Доступ" })).toBeVisible();
    expect(
      await directory.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
    ).toBe(true);
  });

  test("keeps billing controls usable with touch on a narrow screen", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => {
      window.localStorage.setItem("ui:density", "touch");
    });
    await loginInBrowser(page, OWNER);
    await page.goto("/billing");
    await selectTouchDensity(page);

    await expect(
      page.getByRole("heading", { level: 1, name: "Тариф и оплата", exact: true }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
    await expect(page.getByRole("region", { name: "Сводка по тарифу и оплате" })).toBeVisible();

    const invoiceSearch = page.getByRole("searchbox", { name: "Номер счёта" });
    const searchBounds = await invoiceSearch.boundingBox();
    expect(searchBounds).not.toBeNull();
    expect(searchBounds!.height).toBeGreaterThanOrEqual(48);
    const filterBounds = await page.getByRole("button", { name: "Фильтры" }).boundingBox();
    expect(filterBounds).not.toBeNull();
    expect(filterBounds!.height).toBeGreaterThanOrEqual(44);
    await expectNoHorizontalOverflow(page, "/billing touch @ 390x844");
  });

  test("keeps the login form usable at the narrow Windows app width", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto("/login");

    await expect(page.getByText("Aurum Pharma", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 1, name: "Вход в систему", exact: true }),
    ).toBeVisible();
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
      await page.getByPlaceholder("Название, МНН или штрихкод").evaluate((element) => {
        return element.getBoundingClientRect().height;
      }),
    ).toBeGreaterThanOrEqual(44);
    await page.keyboard.press("Escape");
    await expectNoHorizontalOverflow(page, "/catalog touch @ 390x844");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
    expect(await page.evaluate(() => window.localStorage.getItem("ui:density"))).toBe("touch");
  });

  test("keeps the catalog usable at intermediate desktop widths", async ({ page }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1280, height: 800 },
      { width: 768, height: 800 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/catalog");
      await expect(page.getByRole("heading", { level: 1, name: "Каталог" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Сводка по каталогу" })).toBeVisible();
      await expect(page.getByRole("button", { name: /^Открыть карточку / }).first()).toBeVisible();
      await expectNoHorizontalOverflow(page, `/catalog @ ${viewport.width}x${viewport.height}`);
    }
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
