import { test, type Page } from "@playwright/test";

import { expect, loginInBrowser, OWNER } from "./helpers";

const DESKTOP_USER_AGENT_TOKEN = "AurumPharmaDesktop";

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

  test("detects the Windows desktop bridge and notifies the host", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.addInitScript(() => {
      type DesktopMessage = {
        readonly type?: string;
      };
      type DesktopTarget = Window & {
        __aurumDesktopMessages?: DesktopMessage[];
        aurumDesktop?: {
          readonly appVersion: string;
          readonly capabilities: readonly string[];
          readonly platform: "windows";
          postMessage(message: DesktopMessage): void;
        };
      };

      const target = window as DesktopTarget;
      target.__aurumDesktopMessages = [];
      target.aurumDesktop = {
        appVersion: "0.1.0-e2e",
        capabilities: [
          "receipt-print",
          "barcode-scanner",
          "cash-drawer",
          "file-export",
        ],
        platform: "windows",
        postMessage(message) {
          target.__aurumDesktopMessages?.push(message);
        },
      };
    });

    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });

    await expectWindowsDesktopShell(page);
    await expectDesktopMessageType(page, "aurum.desktop.ready");
  });

  test("detects a raw WebView2 bridge before aurumDesktop is injected", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.addInitScript(() => {
      type DesktopMessage = {
        readonly type?: string;
      };
      type WebViewTarget = Window & {
        __aurumDesktopMessages?: DesktopMessage[];
        chrome?: {
          webview?: {
            postMessage(message: DesktopMessage): void;
          };
        };
      };

      const target = window as WebViewTarget;
      target.__aurumDesktopMessages = [];
      const webview = {
        postMessage(message: DesktopMessage) {
          target.__aurumDesktopMessages?.push(message);
        },
      };
      if (target.chrome) {
        Object.defineProperty(target.chrome, "webview", {
          configurable: true,
          value: webview,
        });
      } else {
        Object.defineProperty(target, "chrome", {
          configurable: true,
          value: {
            webview,
          },
        });
      }
    });

    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });

    await expectWindowsDesktopShell(page);
    await expectDesktopMessageType(page, "aurum.desktop.ready");
  });

  test("detects the Windows desktop user-agent token", async ({ browser }) => {
    const context = await browser.newContext({
      userAgent: `Mozilla/5.0 ${DESKTOP_USER_AGENT_TOKEN}`,
    });
    const page = await context.newPage();

    try {
      await page.setViewportSize({ width: 1280, height: 720 });
      await loginInBrowser(page, OWNER);
      await page.goto("/", { waitUntil: "domcontentloaded" });

      await expectWindowsDesktopShell(page);
    } finally {
      await context.close();
    }
  });

  test("shows the online-only warning when the browser goes offline", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await loginInBrowser(page, OWNER);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("runtime-surface-badge")).toBeVisible();
    await expect(page.getByTestId("offline-status-banner")).toHaveCount(0);
    await expect(page.getByTestId("server-status-banner")).toHaveCount(0);

    try {
      await page.context().setOffline(true);

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

  test("locks POS while the API is unavailable and recovers without repeating writes", async ({
    page,
  }) => {
    let serverHealthy = true;
    await page.route("**/healthz", async (route) => {
      await route.fulfill({
        body: JSON.stringify({ status: serverHealthy ? "ok" : "unavailable" }),
        contentType: "application/json",
        status: serverHealthy ? 200 : 503,
      });
    });

    await page.setViewportSize({ width: 1280, height: 720 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos", { waitUntil: "domcontentloaded" });

    const register = page.getByLabel(/^Касса$/);
    await expect(register).toBeVisible();
    if ((await register.evaluate((element) => element.tagName)) === "SELECT") {
      await register.selectOption({ index: 1 });
    }

    const search = page.getByPlaceholder("Найти товар или отсканировать штрих-код");
    await expect(search).toBeVisible();
    await expect(search).toBeEnabled();

    serverHealthy = false;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));

    await expect(page.getByTestId("server-status-banner")).toBeVisible();
    await expect(search).toBeDisabled();

    serverHealthy = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));

    await expect(page.getByTestId("server-status-banner")).toHaveCount(0);
    await expect(search).toBeEnabled();
  });
});

async function expectWindowsDesktopShell(page: Page): Promise<void> {
  await expect(page.locator("html")).toHaveAttribute(
    "data-runtime-surface",
    "windows-desktop",
  );
  await expect(page.getByTestId("runtime-surface-badge")).toHaveText("Windows");
}

async function expectDesktopMessageType(
  page: Page,
  expectedType: string,
): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const target = window as Window & {
          readonly __aurumDesktopMessages?: Array<{ readonly type?: string }>;
        };

        return (target.__aurumDesktopMessages ?? []).map((message) => message.type);
      }),
    )
    .toContain(expectedType);
}
