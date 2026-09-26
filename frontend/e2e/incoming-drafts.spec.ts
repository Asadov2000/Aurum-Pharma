import { type APIResponse, type Request, type Route, request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  catalogSearchKey,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedCatalogItem,
  seedSupplier,
  uniqueName,
} from "./helpers";

// Proxied API failures may include authorization headers in their call log.
test.use({ trace: "off" });

async function fetchDraftRequest(route: Route): Promise<APIResponse> {
  try {
    // Only ECONNRESET is retried. The original row ID, revision and body stay
    // unchanged, and the server must replay these idempotent draft operations.
    return await route.fetch({ maxRetries: 1 });
  } catch {
    // Never attach the original error/cause: it can contain a bearer token.
    throw new Error("Draft request proxy failed after one network retry");
  }
}

test("incomplete incoming input survives lost responses and reload without duplicate items", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Draft branch"));
    const supplier = await seedSupplier(api, uniqueName("Draft supplier"));
    const catalog = await seedCatalogItem(api, uniqueName("Draft medicine"), "5.00");
    const created = await api.post("incoming", {
      data: {
        operation_id: crypto.randomUUID(),
        branch_id: branch.id,
        supplier_id: supplier.id,
        document_date: new Date().toISOString().slice(0, 10),
      },
    });
    expect(created.ok()).toBe(true);
    const incomingDocument = (await created.json()) as { id: string };
    const rowsPath = `incoming/${incomingDocument.id}/draft-rows`;

    await page.setViewportSize({ width: 360, height: 740 });
    await loginInBrowser(page, OWNER);
    await page.goto(`/incoming/${incomingDocument.id}`);

    let droppedSaveResponse = false;
    let droppedSaveRequest: Request | null = null;
    let confirmDroppedSave!: () => void;
    const droppedSave = new Promise<void>((resolve) => {
      confirmDroppedSave = resolve;
    });
    await page.route(`**/api/v1/${rowsPath}/*`, async (route) => {
      if (route.request().method() !== "PUT" || droppedSaveRequest !== null) {
        await route.continue();
        return;
      }
      const payload = route.request().postDataJSON() as { fields?: { qty?: unknown } };
      if (payload.fields?.qty !== "12,") {
        await route.continue();
        return;
      }
      droppedSaveRequest = route.request();
      const response = await fetchDraftRequest(route);
      expect(response.ok()).toBe(true);
      droppedSaveResponse = true;
      await route.abort("failed");
      confirmDroppedSave();
    });

    await page.getByRole("button", { name: "Добавить позицию", exact: true }).click();
    await Promise.all([
      // Synchronize with the actual proxy abort, not browser Request object
      // identity. The UI error and persisted server snapshot remain mandatory.
      droppedSave,
      page.getByLabel("Количество единиц").fill("12,"),
    ]);
    await expect(page.getByText(/Не сохранено/)).toBeVisible();
    expect(droppedSaveResponse).toBe(true);
    const beforeRetry = (await (await api.get(rowsPath)).json()) as {
      id: string;
      fields: { qty: string };
    }[];
    expect(beforeRetry).toHaveLength(1);
    expect(beforeRetry[0]?.fields.qty).toBe("12,");
    await page.getByRole("button", { name: /Повторить сохранение/ }).click();
    await expect(page.getByText("Сохранено в черновике", { exact: true })).toBeVisible();
    // Even invalid, unbroken raw input must not widen the mobile document page.
    await page.getByLabel("Количество единиц").fill("9".repeat(63) + ",");
    await expect(page.getByText("Сохранено в черновике", { exact: true })).toBeVisible();
    await page
      .getByRole("dialog")
      .locator("form")
      .getByRole("button", { name: "Закрыть", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toBeHidden();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await page.getByLabel("Количество единиц").fill("12,");
    await expect(page.getByText("Сохранено в черновике", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Продолжить", exact: true })).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "Принять на склад", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await expect(page.getByLabel("Количество единиц")).toHaveValue("12,");
    await page.screenshot({
      path: test.info().outputPath("incoming-draft-mobile.png"),
      fullPage: true,
    });

    await page
      .getByPlaceholder(/Начните вводить название/)
      .fill(catalogSearchKey(catalog.brand_name));
    await page.getByRole("option", { name: new RegExp(catalog.brand_name) }).click();
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    await page.getByLabel("Срок годности").fill(expiry.toISOString().slice(0, 10));
    await page.getByLabel("Количество единиц").fill("12,5");
    await page.getByLabel("Закупочная цена за единицу").fill("4.00");
    await page.getByLabel("Розничная цена за единицу").fill("5.00");

    let droppedCommitResponse = false;
    let droppedCommitRequest: Request | null = null;
    await page.route(`**/api/v1/${rowsPath}/*/commit`, async (route) => {
      if (route.request().method() !== "POST" || droppedCommitRequest !== null) {
        await route.continue();
        return;
      }
      droppedCommitRequest = route.request();
      const response = await fetchDraftRequest(route);
      expect(response.ok()).toBe(true);
      droppedCommitResponse = true;
      await route.abort("failed");
    });
    await Promise.all([
      page.waitForEvent("requestfailed", {
        predicate: (failed) => failed === droppedCommitRequest && droppedCommitResponse,
      }),
      page.getByRole("button", { name: "Добавить", exact: true }).click(),
    ]);
    await expect(page.getByText(/Ответ не получен. Повторите добавление/)).toBeVisible();
    expect(droppedCommitResponse).toBe(true);
    await page.getByRole("button", { name: "Повторить добавление", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
    await page.reload();

    const saved = (await (await api.get(`incoming/${incomingDocument.id}`)).json()) as {
      items: { qty: string; catalog_id: string }[];
    };
    expect(saved.items).toHaveLength(1);
    expect(saved.items[0]?.qty).toBe("12.500");
    expect(saved.items[0]?.catalog_id).toBe(catalog.id);
    expect(await (await api.get(rowsPath)).json()).toEqual([]);
    await expect(page.getByRole("button", { name: "Продолжить", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Принять на склад", exact: true })).toBeEnabled();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
