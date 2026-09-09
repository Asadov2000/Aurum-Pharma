import { request, test, type Locator, type Page } from "@playwright/test";

import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  uniqueName,
} from "./helpers";

async function expectContainedText(target: Locator, container: Locator) {
  await expect(target).toBeVisible();
  const bounds = await target.boundingBox();
  const parent = await container.boundingBox();
  expect(bounds).not.toBeNull();
  expect(parent).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(parent!.x - 1);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(parent!.x + parent!.width + 1);
  expect(await target.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(
    true,
  );
  // A visible DOM node may still be covered by the adjacent detail panel.
  expect(
    await target.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return hit !== null && (element.contains(hit) || hit.contains(element));
    }),
  ).toBe(true);
}

async function setSidebar(page: Page, compact: boolean) {
  const navigation = page.getByRole("navigation", { name: "Основная навигация" });
  const mode = compact ? "compact" : "expanded";
  if ((await navigation.getAttribute("data-sidebar-mode")) !== mode) {
    await page
      .getByRole("button", {
        name: compact ? "Свернуть боковую панель" : "Развернуть боковую панель",
      })
      .click();
  }
  await expect(navigation).toHaveAttribute("data-sidebar-mode", mode);
}

async function openWorkspace(page: Page, path: string, compact: boolean) {
  const preferencesResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/auth/preferences") &&
      response.request().method() === "GET" &&
      response.ok(),
  );
  await page.goto(path);
  const preferences = (await (await preferencesResponse).json()) as {
    workspace: { desktop_mode: "auto" | "expanded" | "compact" };
  };
  // Full navigation hydrates the account's sidebar mode before local interaction.
  const serverMode = preferences.workspace.desktop_mode;
  await expect(page.getByRole("navigation", { name: "Основная навигация" })).toHaveAttribute(
    "data-sidebar-mode",
    serverMode === "auto" ? "expanded" : serverMode,
  );
  await setSidebar(page, compact);
}

test("keeps supplier status and catalog price readable beside details at desktop widths", async ({
  page,
}) => {
  clearLoginRateLimit(OWNER.email);
  const anonymous = await request.newContext();
  const tokens = await apiLogin(anonymous, OWNER);
  const api = await apiContext(tokens.access_token);
  const catalogName = uniqueName("E2E readable product");
  const supplierName = uniqueName("E2E readable supplier");
  try {
    const product = await api.post("catalog", {
      data: {
        brand_name: catalogName,
        manufacturer: "Производитель с длинным наименованием для проверки ширины",
        dispensing_type: "otc",
        storage_type: "normal",
        base_price: "999999999999.99",
      },
    });
    expect(product.ok()).toBe(true);
    const supplier = await api.post("suppliers", {
      data: {
        operation_id: crypto.randomUUID(),
        name: supplierName,
        legal_name: "Общество с ограниченной ответственностью, длинное наименование",
        contact_person: "Контактное лицо для проверки длинного содержимого",
        email: "readable-layout-supplier@example.com",
        phone: "+992000000000",
        address: "Длинный адрес поставщика для проверки ограничения ширины списка",
      },
    });
    expect(supplier.ok()).toBe(true);
    await loginInBrowser(page, OWNER);

    for (const width of [1536, 1920]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const compact of [false, true]) {
        await openWorkspace(page, "/catalog", compact);
        await page.getByPlaceholder("Название, МНН или штрихкод").fill(catalogName);
        const catalog = page.getByRole("region", { name: "Товары каталога" });
        const row = catalog.locator("article").filter({ hasText: catalogName });
        await expect(catalog.locator("article")).toHaveCount(1);
        await expect(row).toHaveCount(1);
        await expect(
          row.getByRole("button", { name: `Открыть карточку ${catalogName}`, exact: true }),
        ).toBeEnabled();
        const price = row
          .getByText("999999999999.99 TJS", { exact: true })
          .filter({ visible: true });
        await price.scrollIntoViewIfNeeded();
        await expectContainedText(price, row);
        const actions = row.getByRole("button", { name: `Действия для ${catalogName}` });
        const actionsBounds = await actions.boundingBox();
        const textBounds = await price.boundingBox();
        expect(actionsBounds).not.toBeNull();
        expect(textBounds!.x + textBounds!.width).toBeLessThanOrEqual(actionsBounds!.x + 1);
        const priceHeading = catalog.getByText("Цена", { exact: true });
        if (width === 1920) await expect(priceHeading).toBeVisible();
        if (await priceHeading.isVisible()) {
          const headingBounds = await priceHeading.boundingBox();
          const priceBounds = await price.boundingBox();
          expect(Math.abs(headingBounds!.x - priceBounds!.x)).toBeLessThanOrEqual(2);
        }
        if (width === 1536 && !compact) {
          await page.screenshot({ path: test.info().outputPath("catalog-price-1536.png") });
        }

        await openWorkspace(page, "/suppliers", compact);
        await page.getByPlaceholder("Название, контакт, телефон или ИНН").fill(supplierName);
        const table = page.getByRole("table", { name: "Поставщики", exact: true });
        const supplierRow = table.getByRole("row").filter({ hasText: supplierName });
        await expect(table.locator("tbody tr")).toHaveCount(1);
        await expect(supplierRow).toHaveCount(1);
        await expect(
          supplierRow.getByRole("button", { name: `Открыть карточку: ${supplierName}` }),
        ).toBeEnabled();
        const status = supplierRow.getByText("Доступен для приходов", { exact: true });
        await status.scrollIntoViewIfNeeded();
        await expectContainedText(status, table.locator(".."));
        expect(
          await table
            .locator("..")
            .evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        ).toBe(true);
        if (width === 1536 && !compact) {
          await page.screenshot({ path: test.info().outputPath("supplier-status-1536.png") });
        }
      }
    }
  } finally {
    await api.dispose();
    await anonymous.dispose();
  }
});
