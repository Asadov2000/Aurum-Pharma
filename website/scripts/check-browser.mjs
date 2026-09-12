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
try {
  for (const engine of engines) {
    const browser = await playwright[engine].launch();
    try {
      for (const width of [1440, 768, 390, 320]) {
        const context = await browser.newContext({
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
            await page.goto(`${origin}${route}`, { waitUntil: "networkidle" });
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
                await page.keyboard.press(role === "stock" ? "Space" : "Enter");
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
                  await playwright.expect(page.locator(".chart")).toBeVisible();
                  await playwright
                    .expect(page.locator(".mini-list"))
                    .toContainText("Центральная аптека");
                } else {
                  await playwright.expect(page.locator(".chart")).toBeHidden();
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
                `${engine}-${width}-${route === "/" ? "home" : "downloads"}.png`,
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
