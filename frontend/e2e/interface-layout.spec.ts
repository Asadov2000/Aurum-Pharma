import { test, type Page } from "@playwright/test";

import { clearLoginRateLimit, expect, loginInBrowser, OWNER, selectTouchDensity } from "./helpers";

const WORKSPACES = [
  { path: "/", heading: "Главная" },
  { path: "/catalog", heading: "Каталог" },
  { path: "/sales", heading: "Чеки и возвраты" },
  { path: "/users", heading: "Сотрудники" },
  { path: "/roles", heading: "Роли" },
  { path: "/pos", heading: "Касса" },
  { path: "/billing", heading: "Расчёты с Aurum" },
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

// Account contacts and authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

test.describe("Interface layout", () => {
  test.beforeEach(() => {
    clearLoginRateLimit(OWNER.email);
  });

  test("keeps dashboard refresh beside the data timestamp on desktop and mobile", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);

    for (const viewport of [
      { width: 1366, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/");
      const timestamp = page.getByText(/^Данные на /);
      const refresh = page.getByRole("button", { name: "Обновить сводку" });
      await expect(timestamp).toBeVisible();
      await expect(refresh).toHaveText("Обновить сейчас");
      await expect(
        timestamp.locator("..").getByRole("button", { name: "Обновить сводку" }),
      ).toBeVisible();
      await expectNoHorizontalOverflow(page, `dashboard refresh @ ${viewport.width}`);
    }
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
        const heading =
          viewport.width < 1024 && "mobileHeading" in workspace
            ? workspace.mobileHeading
            : workspace.heading;
        await expect(
          page.getByRole("heading", { level: 1, name: heading, exact: true }),
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
      { width: 320, height: 720 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/onboarding");
      await expect(
        page.getByRole("heading", { level: 1, name: "Старт", exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("heading", { name: "Готовность системы" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Постоянный доступ" })).toBeVisible();
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

  test("uses a customizable desktop sidebar and a contained mobile drawer", async ({
    page,
  }, testInfo) => {
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

    // External SVG symbols must actually load and fit, not just exist in the DOM.
    await expect
      .poll(() =>
        desktopNavigation.locator("svg use").evaluateAll(
          (icons) =>
            icons.length > 0 &&
            icons.every((icon) => {
              const bounds = (icon as SVGGraphicsElement).getBBox();
              return (
                bounds.width > 0 &&
                bounds.height > 0 &&
                bounds.x >= -0.5 &&
                bounds.y >= -0.5 &&
                bounds.x + bounds.width <= 20.5 &&
                bounds.y + bounds.height <= 20.5
              );
            }),
        ),
      )
      .toBe(true);
    await desktopNavigation.screenshot({
      path: testInfo.outputPath("sidebar-selected-expanded.png"),
    });

    await page.getByRole("button", { name: "Свернуть боковую панель" }).click();
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "compact");
    const compactBounds = await desktopNavigation.boundingBox();
    expect(compactBounds).not.toBeNull();
    expect(compactBounds!.width).toBeGreaterThanOrEqual(68);
    expect(compactBounds!.width).toBeLessThanOrEqual(76);
    await desktopNavigation.screenshot({
      path: testInfo.outputPath("sidebar-selected-compact.png"),
    });

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

    await page.setViewportSize({ width: 1100, height: 800 });
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "expanded");
    await page.getByRole("button", { name: "Свернуть боковую панель" }).click();
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "compact");
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(desktopNavigation).toHaveAttribute("data-sidebar-mode", "compact");
    await page.getByRole("button", { name: "Развернуть боковую панель" }).click();
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

  test("keeps sidebar actions at the viewport bottom while navigation scrolls", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");

    await page.getByTestId("app-shell-header").evaluate((header) => {
      const notice = document.createElement("div");
      notice.style.height = "48px";
      header.after(notice);
    });

    for (const viewport of [
      { width: 1440, height: 600 },
      { width: 1024, height: 420 },
    ]) {
      await page.setViewportSize(viewport);
      const navigation = page.getByRole("navigation", { name: "Основная навигация" });
      const footer = navigation.getByTestId("sidebar-footer");
      const before = await footer.boundingBox();
      expect(before).not.toBeNull();
      expect(before!.y + before!.height).toBeLessThanOrEqual(viewport.height);

      await navigation.locator(".aurum-scrollbar").evaluate((list) => {
        list.scrollTop = list.scrollHeight;
      });
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      const after = await footer.boundingBox();
      expect(after).not.toBeNull();
      expect(after!.y + after!.height).toBeLessThanOrEqual(viewport.height);
      expect(Math.abs(after!.y + after!.height - (before!.y + before!.height))).toBeLessThanOrEqual(
        1,
      );
    }

    for (const viewport of [
      { width: 390, height: 844 },
      { width: 320, height: 480 },
    ]) {
      await page.setViewportSize(viewport);
      await page.getByRole("button", { name: "Открыть меню" }).click();
      const drawer = page.getByRole("dialog", { name: "Меню приложения" });
      const footer = drawer.getByTestId("sidebar-footer");
      const before = await footer.boundingBox();
      expect(before).not.toBeNull();
      expect(before!.y + before!.height).toBeLessThanOrEqual(viewport.height);

      await drawer.locator(".aurum-scrollbar").evaluate((list) => {
        list.scrollTop = list.scrollHeight;
      });
      const after = await footer.boundingBox();
      expect(after).not.toBeNull();
      expect(after!.y + after!.height).toBeLessThanOrEqual(viewport.height);
      expect(Math.abs(after!.y + after!.height - (before!.y + before!.height))).toBeLessThanOrEqual(
        1,
      );
      await drawer.getByRole("button", { name: "Закрыть меню" }).click();
    }
  });

  test("keeps the role constructor usable on a narrow screen", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginInBrowser(page, OWNER);
    await page.goto("/roles");
    await page.getByRole("button", { name: "Вид интерфейса" }).click();
    for (const [label, field, value] of [
      ["Система", "theme", "system"],
      ["Сенсор", "density", "touch"],
    ] as const) {
      await Promise.all([
        page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname.endsWith("/auth/preferences") &&
            response.request().method() === "PATCH" &&
            response.ok() &&
            Boolean(response.request().postData()?.includes(`"${field}":"${value}"`)),
        ),
        page.getByRole("button", { name: label, exact: true }).click(),
      ]);
    }
    await page.keyboard.press("Escape");

    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      for (const width of [320, 390, 1440]) {
        await test.step(`${colorScheme} @ ${width}`, async () => {
          const height = width < 1440 ? 568 : 900;
          await page.setViewportSize({ width, height });
          await page.goto("/roles");
          await expect(page.locator("html")).toHaveAttribute("data-theme", colorScheme);
          await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
          const createRoleButton = page.locator("header").getByRole("button", {
            name: "Создать роль",
            exact: true,
          });
          await createRoleButton.click();
          const dialog = page.getByRole("dialog", { name: "Создать роль", exact: true });
          await expect(dialog).toBeVisible();
          const bounds = await dialog.boundingBox();
          expect(bounds).not.toBeNull();
          expect(bounds!.x).toBeGreaterThanOrEqual(0);
          expect(bounds!.y).toBeGreaterThanOrEqual(0);
          expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
          expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height);
          await expect(dialog.getByLabel("Название", { exact: true })).toBeVisible();
          if (width < 1280) {
            await dialog.getByRole("button", { name: /^Права доступа · / }).click();
            const groupSelect = dialog.getByLabel("Раздел функций");
            await expect(groupSelect).toBeVisible();
            expect(
              await groupSelect.evaluate((element) => element.getBoundingClientRect().height),
            ).toBeGreaterThanOrEqual(44);
          }
          for (const name of ["Отмена", "Создать роль"]) {
            const footerButton = dialog.getByRole("button", { name, exact: true });
            await expect(footerButton).toBeVisible();
            const footerBounds = await footerButton.boundingBox();
            expect(footerBounds).not.toBeNull();
            expect(footerBounds!.height).toBeGreaterThanOrEqual(44);
            expect(footerBounds!.y).toBeGreaterThanOrEqual(bounds!.y);
            expect(footerBounds!.y + footerBounds!.height).toBeLessThanOrEqual(
              bounds!.y + bounds!.height,
            );
          }
          await expectNoHorizontalOverflow(page, `/roles constructor ${colorScheme} @ ${width}`);
          await dialog.getByRole("button", { name: "Создать роль", exact: true }).click();
          if (width < 1280) {
            await expect(
              dialog.getByRole("button", { name: "О роли", exact: true }),
            ).toHaveAttribute("aria-pressed", "true");
          }
          const roleName = dialog.getByLabel("Название", { exact: true });
          await expect(roleName).toBeFocused();
          await expect(dialog.getByText("Введите название роли")).toBeVisible();
          await roleName.fill("Новая роль");
          await expect(
            dialog.getByText("Есть неопубликованные изменения", { exact: true }),
          ).toBeVisible();
          await expect(
            dialog.getByRole("button", { name: "Создать роль", exact: true }),
          ).toBeEnabled();
          await page.keyboard.press("Escape");
          const discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
          await expect(discardDialog).toBeVisible();
          await discardDialog.getByRole("button", { name: "Выйти без сохранения" }).click();
          await expect(dialog).toBeHidden();
          await expect(createRoleButton).toBeFocused();
        });
      }
    }
  });

  test("keeps the employee directory and profile actions usable with touch", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginInBrowser(page, OWNER);
    await page.goto("/users");
    // Persist appearance through the same settings flow as the user. Local-only
    // preferences can be replaced by the initial server preference response.
    await page.getByRole("button", { name: "Вид интерфейса" }).click();
    for (const [label, field, value] of [
      ["Система", "theme", "system"],
      ["Сенсор", "density", "touch"],
    ] as const) {
      await Promise.all([
        page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname.endsWith("/auth/preferences") &&
            response.request().method() === "PATCH" &&
            response.ok() &&
            Boolean(response.request().postData()?.includes(`"${field}":"${value}"`)),
        ),
        page.getByRole("button", { name: label, exact: true }).click(),
      ]);
    }
    await page.keyboard.press("Escape");

    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      for (const width of [320, 390, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        await page.goto("/users");
        await expect(page.locator("html")).toHaveAttribute("data-theme", colorScheme);
        await expect(page.locator("html")).toHaveAttribute("data-density", "touch");

        const directory = page.getByRole("list", { name: "Сотрудники аптеки" });
        await expect(directory).toBeVisible();
        expect(
          await directory.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        ).toBe(true);
        const profileButton = directory.getByRole("button", {
          name: "Открыть профиль: Demo Owner",
          exact: true,
        });
        const ownerCard = directory.getByRole("listitem").filter({
          has: page.getByRole("button", { name: "Открыть профиль: Demo Owner", exact: true }),
        });
        const actionButton = ownerCard.getByRole("button", {
          name: "Действия для Demo Owner",
          exact: true,
        });
        for (const button of [profileButton, actionButton]) {
          const bounds = await button.boundingBox();
          expect(bounds).not.toBeNull();
          expect(bounds!.width).toBeGreaterThanOrEqual(44);
          expect(bounds!.height).toBeGreaterThanOrEqual(44);
        }
        expect(
          await ownerCard.evaluate((element) =>
            [...element.querySelectorAll("dd")].some((contact) => {
              if (!contact.textContent?.includes("@")) return false;
              const style = getComputedStyle(contact);
              return (
                (style.overflowWrap === "anywhere" || style.wordBreak === "break-all") &&
                contact.scrollWidth <= contact.clientWidth + 1
              );
            }),
          ),
        ).toBe(true);

        await profileButton.click();
        const detailsDialog = page.getByRole("dialog", { name: "Сотрудник: Demo Owner" });
        await expect(detailsDialog).toBeVisible();
        await expect(detailsDialog.getByRole("textbox")).toHaveCount(0);
        await expect(
          detailsDialog.getByRole("button", { name: "Изменить профиль", exact: true }),
        ).toBeVisible();
        const detailsBounds = await detailsDialog.boundingBox();
        expect(detailsBounds).not.toBeNull();
        expect(detailsBounds!.x).toBeGreaterThanOrEqual(0);
        expect(detailsBounds!.x + detailsBounds!.width).toBeLessThanOrEqual(width);
        await expectNoHorizontalOverflow(page, `/users profile ${colorScheme} @ ${width}`);
        await detailsDialog.getByRole("button", { name: "Закрыть", exact: true }).last().click();
        await expect(detailsDialog).toBeHidden();
        await expect(profileButton).toBeFocused();

        await actionButton.click();
        await page.getByRole("menuitem", { name: "Профиль", exact: true }).click();
        const profileDialog = page.getByRole("dialog", { name: "Профиль: Demo Owner" });
        await expect(profileDialog).toBeVisible();
        const dialogBounds = await profileDialog.boundingBox();
        expect(dialogBounds).not.toBeNull();
        expect(dialogBounds!.x).toBeGreaterThanOrEqual(0);
        expect(dialogBounds!.x + dialogBounds!.width).toBeLessThanOrEqual(width);
        await page.keyboard.press("Escape");
        await expect(profileDialog).toBeHidden();
        await expect(actionButton).toBeFocused();
        await expectNoHorizontalOverflow(page, `/users touch ${colorScheme} @ ${width}`);
      }
    }
  });

  test("restores page scrolling after discarding a mobile profile edit and closing the menu", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await loginInBrowser(page, OWNER);
    await page.goto("/users");
    await selectTouchDensity(page);

    const actionButton = page
      .getByRole("list", { name: "Сотрудники аптеки" })
      .getByRole("listitem")
      .filter({
        has: page.getByRole("button", { name: "Открыть профиль: Demo Owner", exact: true }),
      })
      .getByRole("button", { name: "Действия для Demo Owner" });
    await expect(actionButton).toBeVisible();
    const initialOverflow = await page.evaluate(() => document.body.style.overflow);
    expect(initialOverflow).not.toBe("hidden");

    const expectPageScrolls = async () => {
      expect(
        await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight),
      ).toBe(true);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
      await page.mouse.move(300, 500);
      await page.mouse.wheel(0, 400);
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    };
    await expectPageScrolls();

    await actionButton.click();
    await page.getByRole("menuitem", { name: "Профиль", exact: true }).click();
    const profileDialog = page.getByRole("dialog", { name: "Профиль: Demo Owner" });
    await profileDialog.getByLabel("ФИО", { exact: true }).fill("Unsaved profile edit");
    await expect(
      profileDialog.getByRole("button", { name: "Сохранить", exact: true }),
    ).toBeEnabled();
    await profileDialog.getByRole("button", { name: "Закрыть", exact: true }).click();
    const discardDialog = page.getByRole("dialog", { name: "Отменить изменения?" });
    await expect(discardDialog).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(2);
    await discardDialog.getByRole("button", { name: "Выйти без сохранения" }).click();

    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => document.body.style.overflow))
      .toBe(initialOverflow);
    await expectPageScrolls();

    await page.getByRole("button", { name: "Открыть меню", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Меню приложения" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("hidden");
    await drawer.getByRole("button", { name: "Закрыть меню", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => document.body.style.overflow))
      .toBe(initialOverflow);
    await expectPageScrolls();

    await actionButton.click();
    await page.getByRole("menuitem", { name: "Профиль", exact: true }).click();
    await expect(profileDialog.getByLabel("ФИО", { exact: true })).toHaveValue("Demo Owner");
    await profileDialog.getByRole("button", { name: "Закрыть", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
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
      page.getByRole("heading", { level: 1, name: "Расчёты с Aurum", exact: true }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-density", "touch");
    await expect(page.getByRole("heading", { name: "Состояние расчётов" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Подтвержденные платежи" })).toBeVisible();
    await expect(page.getByRole("region", { name: "История счетов" })).toHaveCount(0);
    await page.getByRole("button", { name: "Архив счетов", exact: true }).click();

    const invoiceSearch = page.getByRole("searchbox", { name: "Номер счёта" });
    const searchBounds = await invoiceSearch.boundingBox();
    expect(searchBounds).not.toBeNull();
    expect(searchBounds!.height).toBeGreaterThanOrEqual(48);
    const statusBounds = await page.getByRole("combobox", { name: "Статус" }).boundingBox();
    expect(statusBounds).not.toBeNull();
    expect(statusBounds!.height).toBeGreaterThanOrEqual(44);
    await expect(page.getByRole("button", { name: /^Ещё условия/ })).toHaveCount(0);
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
    await expect(page.getByRole("button", { name: "Войти", exact: true })).toBeVisible();
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

  test("reveals a manually selected theme along the moving cursor", async ({ page }) => {
    await loginInBrowser(page, OWNER);
    await page.goto("/catalog");
    await expect(page.getByRole("heading", { level: 1, name: "Каталог" })).toBeVisible();

    const root = page.locator("html");
    const initialTheme = await root.getAttribute("data-theme");
    const nextTheme = initialTheme === "dark" ? "light" : "dark";
    await page.getByRole("button", { name: "Вид интерфейса" }).click();
    await page.getByRole("button", { name: nextTheme === "dark" ? "Тёмная" : "Светлая" }).click();

    await expect(root).toHaveAttribute("data-theme", nextTheme);
    await expect(root).toHaveClass(/theme-slant-transition/);
    expect(
      await root.evaluate(
        (element) => getComputedStyle(element, "::view-transition-new(root)").clipPath,
      ),
    ).toContain("polygon(");
    const fromRight = await root.evaluate((element) =>
      element.classList.contains("theme-slant-from-right"),
    );
    const edgeBefore = await root.evaluate((element) =>
      Number.parseFloat(element.style.getPropertyValue("--theme-slant-top")),
    );
    await page.mouse.move(fromRight ? 200 : 1000, 384);
    const movedEdge = expect.poll(() =>
      root.evaluate((element) =>
        Number.parseFloat(element.style.getPropertyValue("--theme-slant-top")),
      ),
    );
    if (fromRight) await movedEdge.toBeLessThan(edgeBefore);
    else await movedEdge.toBeGreaterThan(edgeBefore);
    const viewport = page.viewportSize();
    await page.mouse.move(fromRight ? 12 : viewport!.width - 12, 80);
    await expect(root).not.toHaveClass(/theme-slant-transition/, { timeout: 1000 });
    await page.reload();
    await expect(root).toHaveAttribute("data-theme", nextTheme);
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
