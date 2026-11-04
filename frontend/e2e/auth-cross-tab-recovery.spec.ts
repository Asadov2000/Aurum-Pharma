import { expect, test, type Response } from "@playwright/test";

import { clearLoginRateLimit, currentTotp, OWNER } from "./helpers";

// Authentication cookies, account contacts and access tokens must not enter artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" });

test("a second tab cannot rotate the session before a recovered request completes", async ({
  page,
  context,
}) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1366, height: 900 });
  clearLoginRateLimit(OWNER.email);
  // Authenticate through the real UI: no access or refresh token enters the test process.
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel(/Пароль/).fill(OWNER.password);
  await page.getByRole("button", { name: /^Войти$/ }).click();
  await page.getByLabel("Код подтверждения").fill(currentTotp(OWNER.totpSecret!));
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", {
      name: "Партии",
      exact: true,
    }),
  ).toBeVisible();

  const firstLoaded = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/batches" && response.status() === 200;
  });
  await page.goto("/batches", { waitUntil: "domcontentloaded" });
  await firstLoaded;
  await expect(page.getByRole("heading", { name: "Партии", exact: true })).toBeVisible();

  const other = await context.newPage();
  const marker = `cross-tab-recovery-${crypto.randomUUID()}`;
  const isFilteredBatches = (url: URL) =>
    url.pathname === "/api/v1/batches" && url.searchParams.get("batch_number") === marker;
  const isRefresh = (response: Response) =>
    new URL(response.url()).pathname === "/api/v1/auth/refresh";
  const batchStatuses: number[] = [];
  const competingRefreshStatuses: number[] = [];
  let batchAttempts = 0;
  let retryHeld = false;
  let observeCompetingRefresh = false;
  let releaseRetry!: () => void;
  const retryBarrier = new Promise<void>((resolve) => {
    releaseRetry = resolve;
  });
  const recordBatchStatus = (response: Response) => {
    if (isFilteredBatches(new URL(response.url()))) batchStatuses.push(response.status());
  };
  const recordCompetingRefresh = (response: Response) => {
    if (observeCompetingRefresh && isRefresh(response))
      competingRefreshStatuses.push(response.status());
  };

  try {
    // The same browser context shares the httpOnly refresh cookie. This real bootstrap
    // rotates the backend session and makes the first tab's in-memory access token stale.
    const rotated = other.waitForResponse(isRefresh);
    const catalogLoaded = other.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/catalog" && response.status() === 200,
    );
    await other.goto("/catalog", { waitUntil: "domcontentloaded" });
    expect((await rotated).status()).toBe(200);
    await catalogLoaded;
    await expect(other.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
    await expect(other.getByRole("heading", { name: "Каталог", exact: true })).toBeVisible();

    page.on("response", recordBatchStatus);
    other.on("response", recordCompetingRefresh);
    await page.route(isFilteredBatches, async (route) => {
      batchAttempts += 1;
      if (batchAttempts === 2) {
        // Hold only the transport of the retry. Its Authorization header is the real
        // one installed by the application after its successful refresh; do not read it.
        retryHeld = true;
        await retryBarrier;
      }
      await route.continue();
    });
    await page.getByLabel("Номер партии", { exact: true }).fill(marker);
    await expect.poll(() => batchStatuses).toEqual([401]);
    await expect.poll(() => retryHeld).toBe(true);
    expect(batchAttempts).toBe(2);

    // Reload removes the other tab's memory token and requests another genuine refresh.
    // With the fix it queues behind the first tab's refresh+retry transaction. Before
    // the fix it completes, revoking the retry's session while that retry is held.
    observeCompetingRefresh = true;
    await other.reload({ waitUntil: "domcontentloaded" });
    await expect
      .poll(async () => {
        if (competingRefreshStatuses.length > 0) return true;
        return other.evaluate(async () => {
          const locks = await navigator.locks.query();
          return locks.pending?.some((lock) => lock.name === "aurum:auth-cookie") === true;
        });
      })
      .toBe(true);

    releaseRetry();
    await expect.poll(() => batchStatuses).toEqual([401, 200]);
    await expect.poll(() => competingRefreshStatuses).toEqual([200]);
    expect(batchAttempts).toBe(2);
    await expect(page.getByText("Партии не найдены", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Партии", exact: true })).toBeVisible();
    await expect(other.getByRole("heading", { name: "Каталог", exact: true })).toBeVisible();
  } finally {
    // Every failure path releases the held request; a fixed queued refresh cannot hang cleanup.
    releaseRetry();
    page.off("response", recordBatchStatus);
    other.off("response", recordCompetingRefresh);
    await page.unroute(isFilteredBatches);
    await other.close();
  }
});
