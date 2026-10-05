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

test("saves historical pack contents in receiving without activating a unit balance", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Pack contents branch"));
    const supplier = await seedSupplier(api, uniqueName("Pack contents supplier"));
    const name = uniqueName("Pack contents medicine");
    const product = await seedCatalogItem(api, name);
    const settings = {
      version: 1,
      state: "prepared",
      unit: "tablet",
      units_per_pack: 20,
      dispense_step: 1,
      unit_price: "0.50",
    };
    expect(
      (
        await api.patch(`catalog/${product.id}`, { data: { unit_sale_preparation: settings } })
      ).ok(),
    ).toBe(true);
    const documentResponse = await api.post("incoming", {
      data: {
        operation_id: crypto.randomUUID(),
        branch_id: branch.id,
        supplier_id: supplier.id,
        document_date: new Date().toISOString().slice(0, 10),
      },
    });
    expect(documentResponse.ok()).toBe(true);
    const document = (await documentResponse.json()) as { id: string };
    const expiry = new Date();
    expiry.setUTCDate(expiry.getUTCDate() + 180);
    const itemResponse = await api.post(`incoming/${document.id}/items`, {
      data: {
        operation_id: crypto.randomUUID(),
        catalog_id: product.id,
        expires_at: expiry.toISOString().slice(0, 10),
        qty: "2",
        purchase_price: "5.00",
        sale_price: "10.00",
      },
    });
    expect(itemResponse.ok()).toBe(true);
    await page.setViewportSize({ width: 360, height: 740 });
    await loginInBrowser(page, OWNER);
    await page.goto(`/incoming/${document.id}`);
    const open = () =>
      page
        .getByRole("article", { name, exact: true })
        .getByRole("button", { name: "Изменить", exact: true })
        .click();
    const editor = page.getByRole("dialog", { name: "Изменить позицию", exact: true });
    await open();
    const choice = editor.getByRole("checkbox", { name: "Сохранить состав упаковки" });
    await expect(choice).not.toBeChecked();
    await editor.getByText("Сохранить состав упаковки", { exact: true }).click();
    await expect(editor.getByText(/Количество и обе цены остаются за упаковку/)).toBeVisible();
    await editor.getByLabel("Количество единиц").fill("2.5");
    await editor.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(editor.getByRole("alert")).toContainText("целое количество упаковок");
    await editor.getByLabel("Количество единиц").fill("2");
    await editor.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(editor).toBeHidden();

    expect(
      (
        await api.patch(`catalog/${product.id}`, {
          data: {
            unit_sale_preparation: {
              ...settings,
              unit: "capsule",
              units_per_pack: 30,
            },
          },
        })
      ).ok(),
    ).toBe(true);
    await open();
    await expect(choice).toBeChecked();
    await expect(editor.getByText(/Сохранённый состав: 20/)).toBeVisible();
    await editor.getByLabel("Розничная цена за единицу").fill("11.00");
    await editor.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(editor).toBeHidden();
    type Item = {
      unit_packaging_preparation: { units_per_pack: number; unit: string } | null;
      purchase_price: string;
      sale_price: string;
      created_batch_id: string | null;
    };
    const read = async () => {
      const response = await api.get(`incoming/${document.id}`);
      expect(response.ok()).toBe(true);
      return ((await response.json()) as { items: Item[] }).items[0];
    };
    expect((await read()).unit_packaging_preparation?.units_per_pack).toBe(20);
    await open();
    await editor.getByText("Сохранить состав упаковки", { exact: true }).click();
    await editor.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(editor).toBeHidden();
    expect((await read()).unit_packaging_preparation).toBeNull();
    await open();
    await editor.getByText("Сохранить состав упаковки", { exact: true }).click();
    await editor.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(editor).toBeHidden();
    expect((await read()).unit_packaging_preparation).toMatchObject({
      units_per_pack: 30,
      unit: "capsule",
    });
    expect((await api.post(`incoming/${document.id}/accept`)).ok()).toBe(true);
    const accepted = await read();
    expect(accepted.purchase_price).toBe("5.00");
    expect(accepted.sale_price).toBe("11.00");
    const batchResponse = await api.get(`batches/${accepted.created_batch_id}`);
    expect(batchResponse.ok()).toBe(true);
    expect((await batchResponse.json()) as { qty_remaining: string }).toMatchObject({
      qty_remaining: "2.000",
    });
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
