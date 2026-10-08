import { request, test } from "@playwright/test";

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
  seedRegister,
  uniqueName,
} from "./helpers";

test("persists pack, blister and unit draft rows without enabling payment", async ({ page }) => {
  test.setTimeout(120_000);
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  try {
    const branch = await seedBranch(api, uniqueName("Physical editor branch"));
    const register = await seedRegister(api, branch.id, uniqueName("Physical editor register"));
    const medicine = await seedCatalogItem(api, uniqueName("Physical editor medicine"), "10.00");
    expect(
      (
        await api.patch(`catalog/${medicine.id}`, {
          data: {
            unit_sale_preparation: {
              version: 1,
              state: "prepared",
              unit: "tablet",
              units_per_pack: 20,
              units_per_blister: 10,
              dispense_step: 1,
              unit_price: "0.50",
            },
          },
        })
      ).ok(),
    ).toBe(true);

    await page.setViewportSize({ width: 1366, height: 768 });
    await loginInBrowser(page, OWNER);
    await page.goto("/pos");
    await page.getByLabel(/^Касса$/).selectOption({ label: register.name });
    await page.getByLabel("Наличные в кассе на начало смены").fill("100");
    await page.getByRole("button", { name: "Открыть смену" }).click();
    await expect(page.getByText("Смена открыта")).toBeVisible();

    const financialWrites: string[] = [];
    page.on("request", (outgoing) => {
      if (["GET", "HEAD", "OPTIONS"].includes(outgoing.method())) return;
      const path = new URL(outgoing.url()).pathname;
      if (
        /\/sales\/checkout$|\/sales\/[^/]+\/(?:payments|complete)$|\/payment-attempts(?:\/|$)/.test(
          path,
        )
      ) {
        financialWrites.push(path);
      }
    });
    await page.getByRole("button", { name: "Выбрать упаковку, блистер или штуки" }).click();
    const editor = page.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" });
    await expect(editor).toBeVisible();
    let saleId = "";
    for (const [format, label, quantity] of [
      ["Поштучно", "Количество, шт.", "2"],
      ["Блистер", "Количество блистеров", "1"],
      ["Упаковка", "Количество упаковок", "1"],
    ] as const) {
      await editor.getByLabel("Название лекарства").fill(catalogSearchKey(medicine.brand_name));
      await editor.getByRole("option", { name: new RegExp(medicine.brand_name) }).click();
      await editor.getByRole("radio", { name: format, exact: true }).click();
      await editor.getByLabel(label, { exact: true }).fill(quantity);
      const response = page.waitForResponse(
        (result) =>
          result.request().method() === "PUT" &&
          /\/sales\/[^/]+\/physical-cart\/items\/[^/]+$/.test(new URL(result.url()).pathname),
      );
      await editor.getByRole("button", { name: "Добавить в корзину", exact: true }).click();
      const saved = await response;
      expect(saved.ok()).toBe(true);
      saleId = ((await saved.json()) as { sale_id: string }).sale_id;
      await expect(editor.getByLabel("Название лекарства")).toHaveValue("");
    }
    await expect(editor.getByRole("listitem")).toHaveCount(3);
    const read = await api.get(`sales/${saleId}`);
    expect(read.ok()).toBe(true);
    const persisted = (await read.json()) as {
      prepared_cart_items: { sale_format: string; quantity: number }[];
      payments: unknown[];
    };
    expect(persisted.prepared_cart_items.map((line) => [line.sale_format, line.quantity])).toEqual([
      ["unit", 2],
      ["blister", 1],
      ["pack", 1],
    ]);
    expect(persisted.payments).toEqual([]);
    await page.reload();
    await expect(editor).toBeVisible();
    await expect(editor.getByRole("listitem")).toHaveCount(3);
    await expect(editor.getByText(medicine.brand_name, { exact: true })).toHaveCount(3);
    await expect(page.getByRole("region", { name: "К оплате", exact: true })).toHaveCount(0);
    await expect(page.getByText("Оплата пока недоступна", { exact: true })).toBeVisible();
    await page.keyboard.press("F4");
    await page.keyboard.press("Enter");
    await expect(editor).toBeVisible();
    expect(financialWrites).toEqual([]);
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
