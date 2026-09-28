import { request, test, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  CASHIER,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  uniqueName,
} from "./helpers";

// Account contacts and authentication cookies must not enter browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

async function openWorkspace(page: Page, path = "/"): Promise<void> {
  const preferences = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/v1/auth/preferences",
  );
  await page.goto(path, { waitUntil: "domcontentloaded" });
  expect((await preferences).status()).toBe(200);
  await expect(page.getByTestId("app-shell-header")).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
}

async function seedCashierNavigationAccess(): Promise<void> {
  const anonymous = await request.newContext();
  let ownerApi: Awaited<ReturnType<typeof apiContext>> | null = null;
  try {
    ownerApi = await apiContext((await apiLogin(anonymous, OWNER)).access_token);
    const branch = await seedBranch(ownerApi, uniqueName("Header navigation branch"));
    const roleResponse = await ownerApi.post("roles", {
      data: {
        name: uniqueName("Header navigation cashier"),
        description: null,
        permissions: ["pos.sell"],
      },
    });
    expect(roleResponse.status()).toBe(201);
    const role = (await roleResponse.json()) as { id: string };
    const usersResponse = await ownerApi.get("users");
    expect(usersResponse.status()).toBe(200);
    const users = (await usersResponse.json()) as { items: { id: string; email: string }[] };
    const cashier = users.items.find((user) => user.email === CASHIER.email);
    if (!cashier) throw new Error("Cashier fixture is unavailable");
    const assignment = await ownerApi.post(`users/${cashier.id}/assignments`, {
      data: { role_id: role.id, branch_id: branch.id, password_required: true },
    });
    expect(assignment.status()).toBe(201);
  } finally {
    await ownerApi?.dispose();
    await anonymous.dispose();
  }
}

test.describe("Header navigation", () => {
  test("opens the section finder by keyboard and navigates through filtered results", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await openWorkspace(page);

    await page.keyboard.press("Control+k");
    const finder = page.getByRole("dialog", { name: "Перейти в раздел", exact: true });
    const search = finder.getByRole("searchbox", { name: "Найти раздел", exact: true });
    await expect(finder).toBeVisible();
    await expect(search).toBeFocused();
    await search.fill("кат");
    await expect(finder.getByRole("button", { name: "Каталог", exact: true })).toBeVisible();
    await expect(finder.getByRole("button", { name: "Отчёты", exact: true })).toHaveCount(0);
    await finder.getByRole("button", { name: "Каталог", exact: true }).click();
    await expect(page).toHaveURL(/\/catalog(?:\?|$)/);
    await expect(finder).toBeHidden();
    await expect(
      page.getByRole("heading", { name: "Каталог", level: 1, exact: true }),
    ).toBeVisible();

    const trigger = page.getByRole("button", { name: "Перейти в раздел", exact: true });
    await trigger.click();
    await search.fill("отч");
    await finder.getByRole("button", { name: "Отчёты", exact: true }).click();
    await expect(page).toHaveURL(/\/reports(?:\?|$)/);
    await expect(finder).toBeHidden();
  });

  test("uses the sidebar favorites for quick links and preserves them after reload", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await page.setViewportSize({ width: 1440, height: 900 });
    await openWorkspace(page);

    const quickLinks = page.getByRole("navigation", { name: "Быстрые переходы", exact: true });
    const settings = page.getByRole("dialog", { name: "Настроить меню", exact: true });
    await page.getByRole("button", { name: "Настроить быстрые переходы", exact: true }).click();
    await expect(settings).toBeVisible();
    await settings.getByRole("button", { name: "Сбросить", exact: true }).click();
    await settings.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(settings).toBeHidden();
    await expect
      .poll(() =>
        quickLinks
          .getByRole("link")
          .evaluateAll((links) => links.map((link) => link.getAttribute("href"))),
      )
      .toEqual(["/", "/pos", "/catalog", "/incoming", "/reports"]);

    await page.getByRole("button", { name: "Настроить быстрые переходы", exact: true }).click();
    await settings
      .getByRole("button", { name: "Добавить «Каталог» в избранное", exact: true })
      .click();
    await settings.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(quickLinks.getByRole("link")).toHaveCount(1);
    await expect(quickLinks.getByRole("link", { name: "Каталог", exact: true })).toHaveAttribute(
      "href",
      "/catalog",
    );
    await quickLinks.getByRole("link", { name: "Каталог", exact: true }).click();
    await expect(page).toHaveURL(/\/catalog(?:\?|$)/);

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(quickLinks.getByRole("link")).toHaveCount(1);
    await expect(quickLinks.getByRole("link", { name: "Каталог", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.getByRole("button", { name: "Настроить быстрые переходы", exact: true }).click();
    await settings.getByRole("button", { name: "Сбросить", exact: true }).click();
    await settings.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(settings).toBeHidden();
    await expect
      .poll(() =>
        quickLinks
          .getByRole("link")
          .evaluateAll((links) => links.map((link) => link.getAttribute("href"))),
      )
      .toEqual(["/", "/pos", "/catalog", "/incoming", "/reports"]);
  });

  test("filters restricted sections from both the cashier finder and quick links", async ({
    page,
  }) => {
    await seedCashierNavigationAccess();
    await loginInBrowser(page, CASHIER);
    const selfResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === "/api/v1/auth/me",
    );
    await openWorkspace(page, "/pos");
    const self = (await (await selfResponse).json()) as { permissions: string[] };
    expect(self.permissions.includes("reports.view")).toBe(false);

    const quickLinks = page.getByRole("navigation", { name: "Быстрые переходы", exact: true });
    await expect(quickLinks.getByRole("link", { name: "Касса", exact: true })).toBeVisible();
    await expect(quickLinks.locator('a[href="/reports"]')).toHaveCount(0);
    await page.getByRole("button", { name: "Перейти в раздел", exact: true }).click();
    const finder = page.getByRole("dialog", { name: "Перейти в раздел", exact: true });
    await expect(finder.getByRole("button", { name: "Касса", exact: true })).toBeVisible();
    await expect(finder.getByRole("button", { name: "Отчёты", exact: true })).toHaveCount(0);
    await finder.getByRole("searchbox", { name: "Найти раздел", exact: true }).fill("Отчёты");
    await expect(finder.getByRole("button", { name: "Отчёты", exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(finder).toBeHidden();
  });

  test("restores profile-menu focus and opens the authenticated user's own profile", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    const selfResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === "/api/v1/auth/me",
    );
    await openWorkspace(page);
    const self = (await (await selfResponse).json()) as { id: string };

    const trigger = page.getByRole("button", { name: "Меню профиля", exact: true });
    const profileMenu = page.getByRole("dialog", { name: "Меню профиля", exact: true });
    await trigger.click();
    await expect(profileMenu).toBeVisible();
    for (const [name, href] of [
      ["Мой профиль", "/profile"],
      ["Безопасность", "/security"],
      ["Уведомления", "/notifications"],
      ["Моя работа", "/my-work"],
      ["Внешний вид и настройки", "/settings"],
    ] as const) {
      await expect(profileMenu.getByRole("link", { name, exact: true })).toHaveAttribute(
        "href",
        href,
      );
    }
    await expect(profileMenu.getByTestId("runtime-surface-badge")).toBeVisible();
    await expect(page.getByRole("button", { name: "Вид интерфейса", exact: true })).toBeVisible();
    await expect(profileMenu.getByRole("button", { name: "Выйти", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(profileMenu).toBeHidden();
    await expect(trigger).toBeFocused();

    await trigger.click();
    await page.keyboard.press("Control+k");
    const finder = page.getByRole("dialog", { name: "Перейти в раздел", exact: true });
    await expect(finder).toBeVisible();
    await expect(profileMenu).toBeHidden();
    await expect(
      finder.getByRole("searchbox", { name: "Найти раздел", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(finder).toBeHidden();

    await trigger.click();
    const profileResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === "/api/v1/auth/profile",
    );
    await profileMenu.getByRole("link", { name: "Мой профиль", exact: true }).click();
    await expect(page).toHaveURL(/\/profile(?:\?|$)/);
    await expect(profileMenu).toBeHidden();
    const response = await profileResponse;
    expect(response.status()).toBe(200);
    const profile = (await response.json()) as { id: string };
    expect(profile.id === self.id).toBe(true);
    await expect(
      page.getByRole("main").getByRole("heading", { name: "Мой профиль", exact: true }),
    ).toBeVisible();
  });

  test("reflects real browser connectivity and rechecks the server from the connection menu", async ({
    page,
  }) => {
    await loginInBrowser(page, OWNER);
    await openWorkspace(page);
    const trigger = page.getByRole("button", { name: "Состояние соединения", exact: true });
    const connection = page.getByRole("dialog", { name: "Состояние соединения", exact: true });
    await trigger.click();
    await expect(trigger.getByRole("status")).toHaveText("Онлайн");
    await expect(connection).toContainText("Соединение с сервером есть.");

    try {
      await page.context().setOffline(true);
      await expect(trigger.getByRole("status")).toHaveText("Нет сети");
      await expect(connection).toContainText("Нет интернет-соединения.");
      await page.context().setOffline(false);
      await expect(trigger.getByRole("status")).toHaveText("Онлайн");
      const healthResponse = page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/healthz",
      );
      await connection.getByRole("button", { name: "Проверить соединение", exact: true }).click();
      expect((await healthResponse).status()).toBe(200);
      await expect(connection).toBeHidden();
      await expect(trigger.getByRole("status")).toHaveText("Онлайн");
      await trigger.click();
      await page.keyboard.press("Escape");
      await expect(connection).toBeHidden();
      await expect(trigger).toBeFocused();
    } finally {
      await page.context().setOffline(false);
    }
  });

  test("contains the header and its dialogs at 320px and aligns the desktop sidebar", async ({
    page,
  }, testInfo) => {
    await loginInBrowser(page, OWNER);
    await openWorkspace(page);
    const header = page.getByTestId("app-shell-header");
    await page.setViewportSize({ width: 1440, height: 900 });
    const navigation = page.getByRole("navigation", { name: "Основная навигация", exact: true });
    await expect(navigation).toBeVisible();
    await expect
      .poll(async () => {
        const headerBounds = await header.boundingBox();
        const sidebarBounds = await navigation.boundingBox();
        return headerBounds && sidebarBounds
          ? Math.abs(sidebarBounds.y - headerBounds.y - headerBounds.height)
          : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(1);
    await expectNoHorizontalOverflow(page);

    // This crop contains no contact details or business records.
    await header.screenshot({ path: testInfo.outputPath("header-desktop.png") });

    await page.setViewportSize({ width: 320, height: 568 });
    await expectNoHorizontalOverflow(page);
    const headerBounds = await header.boundingBox();
    expect(headerBounds).not.toBeNull();
    expect(headerBounds!.x).toBeGreaterThanOrEqual(0);
    expect(headerBounds!.x + headerBounds!.width).toBeLessThanOrEqual(320);
    await header.screenshot({ path: testInfo.outputPath("header-mobile.png") });
    for (const name of ["Перейти в раздел", "Меню профиля", "Состояние соединения"] as const) {
      await header.getByRole("button", { name, exact: true }).click();
      const dialog = page.getByRole("dialog", { name, exact: true });
      await expect(dialog).toBeVisible();
      const bounds = await dialog.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(568);
      await expectNoHorizontalOverflow(page);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    }
  });
});
