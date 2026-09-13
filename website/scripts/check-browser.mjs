import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { buildSite, repositoryRoot } from "./build.mjs";
import { startPreview } from "./serve.mjs";
import { testOutput } from "../tests/helpers.mjs";

// Reuse the repository's locked browser toolchain; website has no npm dependencies.
const requireFrontend = createRequire(
  path.join(repositoryRoot, "frontend", "package.json"),
);
let playwright;
try {
  playwright = requireFrontend("@playwright/test");
} catch {
  throw new Error(
    "Prepare the existing frontend dependencies before website browser checks (see website/README.md).",
  );
}

const engines = (process.env.WEBSITE_BROWSERS ?? "chromium").split(",");
assert.ok(
  engines.every((engine) => ["chromium", "webkit"].includes(engine)),
  "Unsupported website browser engine",
);
const appUrl = "https://app.example.test/login";
const build = await buildSite({
  appUrl,
  production: true,
  outputRoot: await testOutput(),
});
const server = await startPreview({ root: build.outputRoot, port: 0 });
const origin = `http://127.0.0.1:${server.address().port}`;
const output = path.join(repositoryRoot, ".codex", "website-browser");
await mkdir(output, { recursive: true });
let checks = 0;

async function expectTheme(page, theme) {
  await playwright
    .expect(page.locator("html"))
    .toHaveAttribute("data-theme", theme);
  const toggle = page.getByRole("button", { name: "Тёмная тема", exact: true });
  await playwright.expect(toggle).toBeVisible();
  await playwright
    .expect(toggle)
    .toHaveAttribute("aria-pressed", String(theme === "dark"));
}

async function checkThemePreferences(browser) {
  const context = await browser.newContext({ colorScheme: "light" });
  try {
    const page = await context.newPage();
    await page.goto(origin);
    await expectTheme(page, "light");
    await page.emulateMedia({ colorScheme: "dark" });
    await expectTheme(page, "dark");
    const toggle = page.getByRole("button", {
      name: "Тёмная тема",
      exact: true,
    });
    await toggle.focus();
    await page.keyboard.press("Space");
    await expectTheme(page, "light");
    await playwright.expect(toggle).toBeFocused();
    assert.equal(
      await page.evaluate(() => localStorage.getItem("aurum-website-theme")),
      "light",
    );
    await page.reload();
    await expectTheme(page, "light");
    await page.locator('a[href="/downloads/"]').first().click();
    await expectTheme(page, "light");
    await page
      .getByRole("button", { name: "Тёмная тема", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await expectTheme(page, "dark");
    await page.emulateMedia({ colorScheme: "light" });
    await expectTheme(page, "dark");
    await page.reload();
    await expectTheme(page, "dark");
    await page.evaluate(() =>
      localStorage.setItem("aurum-website-theme", "invalid-theme"),
    );
    await page.reload();
    await expectTheme(page, "light");
    await page.emulateMedia({ colorScheme: "dark" });
    await expectTheme(page, "dark");
    checks += 1;
  } finally {
    await context.close();
  }

  const blocked = await browser.newContext({ colorScheme: "dark" });
  try {
    await blocked.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("Storage is unavailable", "SecurityError");
        },
      });
    });
    const page = await blocked.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (const route of ["/", "/downloads/"]) {
      await page.goto(`${origin}${route}`);
      await expectTheme(page, "dark");
      await page
        .getByRole("button", { name: "Тёмная тема", exact: true })
        .click();
      await expectTheme(page, "light");
      await page.emulateMedia({ colorScheme: "light" });
      await page.emulateMedia({ colorScheme: "dark" });
      await expectTheme(page, "light");
      checks += 1;
    }
    assert.deepEqual(
      errors,
      [],
      "Unavailable storage must not break theme controls",
    );
  } finally {
    await blocked.close();
  }
}

async function checkDarkContrast(page) {
  const ratios = await page.evaluate(() => {
    const channels = (color) => color.match(/[\d.]+/g).map(Number);
    const luminance = (rgb) =>
      rgb
        .slice(0, 3)
        .map((value) => {
          const normalized = value / 255;
          return normalized <= 0.04045
            ? normalized / 12.92
            : ((normalized + 0.055) / 1.055) ** 2.4;
        })
        .reduce(
          (sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index],
          0,
        );
    return [
      "h1",
      ".intro, .hero-description",
      ".eyebrow",
      ".button.primary",
    ].map((selector) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing contrast target: ${selector}`);
      const foreground = luminance(channels(getComputedStyle(element).color));
      let background = [255, 255, 255];
      for (
        let ancestor = element;
        ancestor;
        ancestor = ancestor.parentElement
      ) {
        const color = channels(getComputedStyle(ancestor).backgroundColor);
        if (color.length === 3 || color[3] === 1) {
          background = color;
          break;
        }
      }
      const backdrop = luminance(background);
      return {
        selector,
        ratio:
          (Math.max(foreground, backdrop) + 0.05) /
          (Math.min(foreground, backdrop) + 0.05),
      };
    });
  });
  for (const { selector, ratio } of ratios) {
    assert.ok(
      ratio >= 4.5,
      `Dark theme contrast ${selector}: ${ratio.toFixed(2)} must be at least 4.5`,
    );
  }
}

try {
  for (const engine of engines) {
    const browser = await playwright[engine].launch();
    try {
      await checkThemePreferences(browser);
      for (const theme of ["light", "dark"]) {
        for (const width of [1440, 768, 390, 320]) {
          const context = await browser.newContext({
            colorScheme: theme,
            viewport: { width, height: 900 },
            reducedMotion: "reduce",
            ...(width < 768 ? { isMobile: true, hasTouch: true } : {}),
          });
          try {
            const page = await context.newPage();
            const errors = [];
            const externalRequests = [];
            const policyViolations = [];
            page.on("console", (message) => {
              if (message.type() === "error")
                policyViolations.push(message.text());
            });
            page.on("pageerror", (error) => errors.push(error.message));
            page.on("request", (request) => {
              if (!request.url().startsWith(`${origin}/`))
                externalRequests.push(request.url());
            });
            for (const route of ["/", "/downloads/"]) {
              await page.goto(`${origin}${route}`, {
                waitUntil: "networkidle",
              });
              await expectTheme(page, theme);
              if (theme === "dark") await checkDarkContrast(page);
              await playwright.expect(page.locator("h1")).toBeVisible();
              if (route === "/") {
                for (const [role, title, total, navigation] of [
                  [
                    "cashier",
                    "Покупатель перед вами. Всё нужное рядом.",
                    "20 TJS",
                    "Касса",
                  ],
                  [
                    "stock",
                    "Поставка — в понятном документе",
                    "№ 024",
                    "Приходы",
                  ],
                  ["owner", "Что происходит в аптеке", "1 320 TJS", "Обзор"],
                ]) {
                  const button = page.locator(`[data-role="${role}"]`);
                  await button.focus();
                  await page.keyboard.press(
                    role === "stock" ? "Space" : "Enter",
                  );
                  await playwright.expect(button).toBeFocused();
                  await playwright
                    .expect(button)
                    .toHaveAttribute("aria-pressed", "true");
                  await playwright
                    .expect(page.locator('[data-role][aria-pressed="true"]'))
                    .toHaveCount(1);
                  await playwright
                    .expect(page.locator("#preview-title"))
                    .toHaveText(title);
                  await playwright
                    .expect(page.locator("#value-a"))
                    .toHaveText(total);
                  await playwright
                    .expect(page.locator(".app-nav .selected"))
                    .toHaveText(navigation);
                  if (role === "owner") {
                    await playwright
                      .expect(page.locator(".chart"))
                      .toBeVisible();
                    await playwright
                      .expect(page.locator(".mini-list"))
                      .toContainText("Центральная аптека");
                  } else {
                    await playwright
                      .expect(page.locator(".chart"))
                      .toBeHidden();
                    await playwright
                      .expect(page.locator(".mini-list"))
                      .toBeVisible();
                    await playwright
                      .expect(page.locator(".mini-list"))
                      .toContainText("Бинт медицинский");
                  }
                  assert.ok(
                    await page.evaluate(
                      () =>
                        document.documentElement.scrollWidth <=
                        document.documentElement.clientWidth + 1,
                    ),
                    `${width}: ${role} overflow`,
                  );
                  checks += 1;
                }
                for (const role of ["cashier", "stock", "owner"]) {
                  await page.locator(`[data-preview="${role}"]`).click();
                  await playwright
                    .expect(page.locator(`[data-role="${role}"]`))
                    .toHaveAttribute("aria-pressed", "true");
                  await playwright.expect(page).toHaveURL(`${origin}/#preview`);
                  const offset = await page
                    .locator("#preview")
                    .evaluate((element) => element.getBoundingClientRect().top);
                  assert.ok(
                    offset >= 0 && offset < 100,
                    "Feature link must reveal its example",
                  );
                  checks += 1;
                }
              }
              const geometry = await page.evaluate(() => ({
                viewport: document.documentElement.clientWidth,
                content: document.documentElement.scrollWidth,
              }));
              assert.ok(
                geometry.content <= geometry.viewport + 1,
                `${engine} ${width} ${route}: horizontal overflow`,
              );
              const login = page.locator(`a[href="${appUrl}"]`).first();
              await playwright.expect(login).toBeVisible();
              await playwright
                .expect(page.locator('a[href$=".msix"], a[href$=".exe"]'))
                .toHaveCount(0);
              const details = page.locator("details").first();
              if (await details.count()) {
                const summary = details.locator("summary");
                await summary.focus();
                await page.keyboard.press("Enter");
                await playwright.expect(details).toHaveAttribute("open", "");
              }
              await page.evaluate(() =>
                window.scrollTo(0, document.documentElement.scrollHeight),
              );
              assert.ok(
                await page.evaluate(() => window.scrollY > 0),
                "Page must remain vertically scrollable",
              );
              await page.evaluate(() => window.scrollTo(0, 0));
              await page.screenshot({
                path: path.join(
                  output,
                  `${engine}-${width}-${route === "/" ? "home" : "downloads"}-${theme}.png`,
                ),
                fullPage: true,
              });
              checks += 1;
            }
            await page.goto(origin);
            await page.locator('a[href="/downloads/"]').first().click();
            await playwright.expect(page).toHaveURL(`${origin}/downloads/`);
            assert.deepEqual(
              externalRequests,
              [],
              "Public pages must not call app APIs, analytics or external resources",
            );
            assert.deepEqual(
              errors,
              [],
              "Public pages must not emit browser errors",
            );
            assert.deepEqual(policyViolations, [], "No console or CSP errors");
            await page.route(appUrl, (route) =>
              route.fulfill({
                contentType: "text/html",
                body: "<h1>Application login destination</h1>",
              }),
            );
            await page.locator(`a[href="${appUrl}"]`).first().click();
            await playwright.expect(page).toHaveURL(appUrl);
            await playwright
              .expect(
                page.getByRole("heading", {
                  name: "Application login destination",
                }),
              )
              .toBeVisible();
            checks += 2;
          } finally {
            await context.close();
          }
        }
      }
    } finally {
      await browser.close();
    }
  }
  process.stdout.write(
    `Website browser checks passed: ${checks}; engines: ${engines.join(", ")}.\n`,
  );
} finally {
  await new Promise((resolve) => server.close(resolve));
}
