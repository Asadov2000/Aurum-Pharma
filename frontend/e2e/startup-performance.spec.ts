import { test } from "@playwright/test";

import { expect, loginInBrowser, OWNER } from "./helpers";

const OPTIONAL_AUTH_COMPONENT = /\/assets\/(?:LoginPage|MfaStepUpDialog)-[^/]+\.js$/;

test("keeps optional authentication code out of an authenticated startup", async ({ page }) => {
  const loadedScripts: string[] = [];
  page.on("response", (response) => {
    if (response.request().resourceType() !== "script") return;
    loadedScripts.push(new URL(response.url()).pathname);
  });

  await loginInBrowser(page, OWNER);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1, name: "Главная", exact: true })).toBeVisible();

  expect(loadedScripts.filter((path) => OPTIONAL_AUTH_COMPONENT.test(path))).toEqual([]);

  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { level: 1, name: "Вход в систему", exact: true }),
  ).toBeVisible();

  expect(loadedScripts.some((path) => path.includes("/assets/LoginPage-"))).toBe(true);
  expect(loadedScripts.some((path) => path.includes("/assets/MfaStepUpDialog-"))).toBe(false);
});
