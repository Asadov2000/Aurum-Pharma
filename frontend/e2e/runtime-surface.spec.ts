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

    await expect(page.locator("html")).toHaveAttribute(
      "data-runtime-surface",
      "windows-desktop",
    );
    await expect(page.getByTestId("runtime-surface-badge")).toHaveText("Windows");
    await expect
      .poll(() =>
        page.evaluate(() => {
          const target = window as Window & {
            readonly __aurumDesktopMessages?: Array<{ readonly type?: string }>;
          };

          return (target.__aurumDesktopMessages ?? []).map((message) => message.type);
        }),
      )
      .toContain("aurum.desktop.ready");
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
      await page.evaluate(() => {
        Object.defineProperty(window.navigator, "onLine", {
          configurable: true,
          value: false,
        });
        window.dispatchEvent(new Event("offline"));
      });

      const banner = page.getByTestId("offline-status-banner");
      await expect(banner).toBeVisible();
      await expect(banner).toContainText("Касса работает только онлайн");
      await expect(
        page.locator("main#main-content").getByTestId("offline-status-banner"),
      ).toHaveCount(0);
    } finally {
      await page.evaluate(() => {
        Object.defineProperty(window.navigator, "onLine", {
          configurable: true,
          value: true,
        });
        window.dispatchEvent(new Event("online"));
      });
    }
  });
});
