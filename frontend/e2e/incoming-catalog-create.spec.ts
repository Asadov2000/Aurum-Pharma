import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedCatalogItem,
  seedSupplier,
  uniqueName,
} from "./helpers";

test("creates a product and barcode inside an incoming row after a lost response without losing input", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Quick create branch"));
    const supplier = await seedSupplier(api, uniqueName("Quick create supplier"));
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
    const name = uniqueName("New incoming medicine");
    const barcode = `AURUM-${crypto.randomUUID()}`;
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    const expiryDate = expiry.toISOString().slice(0, 10);

    await page.setViewportSize({ width: 360, height: 740 });
    await loginInBrowser(page, OWNER);
    await page.goto(`/incoming/${incomingDocument.id}`);
    await page.getByRole("button", { name: "Добавить позицию", exact: true }).click();
    const incomingDialog = page.getByRole("dialog", { name: "Добавить позицию", exact: true });
    await incomingDialog.getByLabel("Номер партии").fill("NEW-BATCH");
    await incomingDialog.getByLabel("Количество единиц").fill("4,5");
    await incomingDialog.getByLabel("Срок годности").fill(expiryDate);
    await incomingDialog.getByLabel("Закупочная цена за единицу").fill("4.00");
    await incomingDialog.getByLabel("Розничная цена за единицу").fill("6.00");
    await incomingDialog.getByPlaceholder(/Начните вводить название/).fill(name);
    await incomingDialog.getByRole("button", { name: `Проверить и создать «${name}»` }).click();

    const productDialog = page.getByRole("dialog", { name: "Добавить товар", exact: true });
    await expect(productDialog.getByLabel("Торговое название")).toHaveValue(name);
    await expect(productDialog.getByLabel("Условия отпуска", { exact: true })).toHaveValue("");
    await expect(productDialog.getByLabel("Условия хранения", { exact: true })).toHaveValue("");
    await productDialog.getByLabel("Условия отпуска", { exact: true }).selectOption("otc");
    await productDialog.getByLabel("Условия хранения", { exact: true }).selectOption("normal");
    await productDialog.getByLabel("Первый штрихкод (необязательно)").fill(barcode);
    await productDialog.getByLabel("Тип штрихкода", { exact: true }).selectOption("code128");

    const creationRequests: unknown[] = [];
    let createdItemId: string | undefined;
    await page.route("**/api/v1/catalog", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      creationRequests.push(route.request().postDataJSON());
      if (createdItemId) {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      createdItemId = ((await response.json()) as { id: string }).id;
      await route.abort("failed");
    });
    await productDialog.getByRole("button", { name: "Добавить товар", exact: true }).click();
    await expect(productDialog.getByText(/Ответ не получен/)).toBeVisible();
    await expect(productDialog.getByLabel("Торговое название")).toBeDisabled();
    await productDialog.getByLabel("Торговое название").scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath("incoming-product-mobile.png") });
    await productDialog.getByRole("button", { name: "Повторить создание", exact: true }).click();

    await expect(productDialog).toBeHidden();
    await expect(incomingDialog).toBeVisible();
    await expect(incomingDialog.getByPlaceholder(/Начните вводить название/)).toHaveValue(name);
    await expect(incomingDialog.getByLabel("Количество единиц")).toHaveValue("4,5");
    await expect(incomingDialog.getByLabel("Номер партии")).toHaveValue("NEW-BATCH");
    await expect(incomingDialog.getByLabel("Срок годности")).toHaveValue(expiryDate);
    await expect(incomingDialog.getByLabel("Закупочная цена за единицу")).toHaveValue("4.00");
    await expect(incomingDialog.getByLabel("Розничная цена за единицу")).toHaveValue("6.00");
    await expect(incomingDialog.getByText("Выберите позицию", { exact: true })).toHaveCount(0);
    await expect(incomingDialog.getByText("Сохранено в черновике", { exact: true })).toBeVisible();
    expect(creationRequests).toHaveLength(2);
    expect(creationRequests[1]).toEqual(creationRequests[0]);
    expect(createdItemId).toBeTruthy();

    const productResponse = await api.get(`catalog/${createdItemId}`);
    expect(productResponse.ok()).toBe(true);
    const product = (await productResponse.json()) as {
      barcodes: { code: string; code_type: string }[];
    };
    expect(product.barcodes).toHaveLength(1);
    expect(product.barcodes[0]).toMatchObject({ code: barcode, code_type: "code128" });
    const beforeCommit = (await (await api.get(`incoming/${incomingDocument.id}`)).json()) as {
      items: unknown[];
    };
    expect(beforeCommit.items).toHaveLength(0);
    const rows = (await (await api.get(`incoming/${incomingDocument.id}/draft-rows`)).json()) as {
      fields: { catalog_id: string; qty: string };
    }[];
    expect(rows).toHaveLength(1);
    expect(rows[0]?.fields).toMatchObject({ catalog_id: createdItemId, qty: "4,5" });

    await incomingDialog
      .locator("form")
      .getByRole("button", { name: "Закрыть", exact: true })
      .click();
    await page.reload();
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await expect(incomingDialog.getByPlaceholder(/Начните вводить название/)).toHaveValue(name);
    await expect(incomingDialog.getByLabel("Количество единиц")).toHaveValue("4,5");
    await incomingDialog.getByRole("button", { name: "Добавить", exact: true }).click();
    await expect(incomingDialog).toBeHidden();
    const finalDocument = (await (await api.get(`incoming/${incomingDocument.id}`)).json()) as {
      items: { catalog_id: string; qty: string }[];
    };
    expect(finalDocument.items).toHaveLength(1);
    expect(finalDocument.items[0]).toMatchObject({ catalog_id: createdItemId, qty: "4.500" });
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

test("selects an existing catalog card from incoming creation without losing the draft", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Existing card branch"));
    const supplier = await seedSupplier(api, uniqueName("Existing card supplier"));
    const name = uniqueName("Existing incoming medicine");
    const product = await seedCatalogItem(api, name);
    const created = await api.post("incoming", {
      data: {
        operation_id: crypto.randomUUID(),
        branch_id: branch.id,
        supplier_id: supplier.id,
        document_date: new Date().toISOString().slice(0, 10),
      },
    });
    expect(created.ok()).toBe(true);
    const document = (await created.json()) as { id: string };

    await loginInBrowser(page, OWNER);
    await page.goto(`/incoming/${document.id}`);
    await page.getByRole("button", { name: "Добавить позицию", exact: true }).click();
    const incomingDialog = page.getByRole("dialog", { name: "Добавить позицию", exact: true });
    await incomingDialog.getByLabel("Номер партии").fill("EXISTING-BATCH");
    await incomingDialog.getByLabel("Количество единиц").fill("7");
    await incomingDialog.getByLabel("Закупочная цена за единицу").fill("4.00");
    await incomingDialog.getByLabel("Розничная цена за единицу").fill("6.00");
    await incomingDialog.getByPlaceholder(/Начните вводить название/).fill(name);
    await incomingDialog.getByRole("button", { name: `Проверить и создать «${name}»` }).click();

    const productDialog = page.getByRole("dialog", { name: "Добавить товар", exact: true });
    await expect(productDialog.getByText("Проверьте, нет ли товара в каталоге")).toBeVisible();
    await productDialog
      .locator("li")
      .filter({ hasText: name })
      .getByRole("button", { name: "Выбрать этот товар" })
      .click();
    await expect(productDialog).toBeHidden();
    await expect(incomingDialog.getByLabel("Количество единиц")).toHaveValue("7");
    await expect(incomingDialog.getByLabel("Номер партии")).toHaveValue("EXISTING-BATCH");
    const rows = (await (await api.get(`incoming/${document.id}/draft-rows`)).json()) as {
      fields: { catalog_id: string; qty: string };
    }[];
    expect(rows).toHaveLength(1);
    expect(rows[0]?.fields).toMatchObject({ catalog_id: product.id, qty: "7" });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
