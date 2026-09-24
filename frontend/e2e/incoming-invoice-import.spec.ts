import { request, test } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedSupplier,
  uniqueName,
} from "./helpers";
test.use({ trace: "off" });

test("supplier CSV becomes reviewable draft rows without duplicate import", async ({ page }) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Invoice branch"));
    const supplier = await seedSupplier(api, uniqueName("Invoice supplier"));
    const created = await api.post("incoming", {
      data: {
        operation_id: crypto.randomUUID(),
        branch_id: branch.id,
        supplier_id: supplier.id,
        document_date: new Date().toISOString().slice(0, 10),
      },
    });
    expect(created.ok()).toBe(true);
    const doc = (await created.json()) as { id: string };
    const productName = uniqueName("Новый препарат из накладной");
    const contents = Buffer.from(
      `Номи ассортимент;Миқдор;Нарх;Фаъолият;Серия\n${productName};2;10,50;2027-12-31;AB-1\n`,
      "utf-8",
    );

    await loginInBrowser(page, OWNER);
    await page.goto(`/incoming/${doc.id}`);
    await page.getByRole("button", { name: "Загрузить накладную" }).click();
    await page.getByLabel("Файл накладной").setInputFiles({
      name: "supplier.csv",
      mimeType: "text/csv",
      buffer: contents,
    });
    await page.getByRole("button", { name: "Проверить файл" }).click();
    await expect(page.getByText("Найдено строк: 1.")).toBeVisible();
    await expect(page.getByLabel("Название товара *")).toHaveValue("0");
    await expect(page.getByLabel("Количество")).toHaveValue("1");
    await expect(page.getByLabel("Закупочная цена")).toHaveValue("2");
    await expect(page.getByLabel("Цена продажи")).toHaveValue("");
    await page.getByRole("button", { name: "Добавить 1 строк в черновик" }).click();
    await expect(page.getByRole("dialog", { name: "Загрузить накладную" })).toBeHidden();
    await expect(page.getByText(/1 строк добавлено в черновик/)).toBeVisible();

    const rowsPath = `incoming/${doc.id}/draft-rows`;
    const initialRows = (await (await api.get(rowsPath)).json()) as {
      fields: { source_name: string; sale_price: string; purchase_price: string };
    }[];
    expect(initialRows).toHaveLength(1);
    expect(initialRows[0]?.fields).toMatchObject({
      source_name: productName,
      purchase_price: "10,50",
      sale_price: "",
    });

    // Reopening the same source with another client operation must not append
    // rows, including after the browser has lost its original import state.
    const retry = await api.post(`incoming/${doc.id}/invoice/import`, {
      multipart: {
        file: { name: "supplier.csv", mimeType: "text/csv", buffer: contents },
        mapping: JSON.stringify({
          source_name: 0,
          qty: 1,
          purchase_price: 2,
          sale_price: null,
          batch_number: 4,
          expires_at: 3,
          manufactured_at: null,
        }),
        operation_id: crypto.randomUUID(),
      },
    });
    expect(retry.ok()).toBe(true);
    expect(await retry.json()).toMatchObject({ imported_count: 0, existing_count: 1 });

    await page.reload();
    await expect(page.getByText(productName, { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Принять на склад" })).toBeDisabled();
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByPlaceholder(/Начните вводить название/)).toHaveValue(productName);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
