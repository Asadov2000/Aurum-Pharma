import { test, type Page, type Route } from "@playwright/test";

import { expect, loginInBrowser, OWNER } from "./helpers";

const BRANCH_ID = "20000000-0000-4000-8000-000000000002";

const metadata = {
  snapshot_id: "20000000-0000-4000-8000-000000000003",
  source_revision: 7,
  source_generated_at: "2026-09-20T09:00:00Z",
  last_confirmed_at: "2026-09-20T10:00:00Z",
  age_seconds: 30,
  is_stale: false,
  source: "local_edge",
};

const catalogItem = {
  id: "30000000-0000-4000-8000-000000000001",
  tenant_id: "10000000-0000-4000-8000-000000000001",
  brand_name: "Парацетамол Edge",
  inn: "Парацетамол",
  manufacturer: "Aurum",
  form: "таблетки",
  dosage: "500 мг",
  pack_size: "10",
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "5.00",
  currency: "TJS",
  image_version: null,
  is_active: true,
  deleted_at: null,
  created_at: "2026-09-20T09:00:00Z",
  updated_at: "2026-09-20T09:00:00Z",
};

test("keeps signed Edge reads available without WAN and hides mutations", async ({ page }) => {
  await installEdgeBridge(page);
  await routeEdgeReadModel(page);
  await page.route("**/healthz", async (route) => {
    await route.fulfill({ contentType: "application/json", status: 200, body: '{"status":"ok"}' });
  });

  await loginInBrowser(page, OWNER);

  await page.goto("/catalog", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Парацетамол Edge").first()).toBeVisible();
  await expect(page.getByText("Локальные данные · только чтение.")).toBeVisible();
  await expect(page.getByText("Снимок подтверждён:")).toBeVisible();
  await expect(page.getByRole("button", { name: /добавить товар/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /загрузить из excel/i })).toHaveCount(0);

  await page.goto("/suppliers", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Поставщик Edge")).toBeVisible();
  await expect(page.getByRole("button", { name: /добавить поставщика/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /оформить возврат/i })).toHaveCount(0);

  await page.goto("/batches", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Партия EDGE-001")).toBeVisible();
  await expect(page.getByText("12 ед.")).toBeVisible();
  await expect(page.getByRole("button", { name: /списать/i })).toHaveCount(0);
  await expect(page.getByText(/локальный сервис недоступен/i)).toHaveCount(0);
});

async function installEdgeBridge(page: Page): Promise<void> {
  await page.addInitScript((branchId) => {
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(window, "aurumDesktop", {
      configurable: false,
      enumerable: true,
      value: {
        appVersion: "0.2.0-e2e",
        capabilities: [],
        dataPlane: { mode: "edge-readonly", branchId },
        platform: "windows",
        postMessage() {},
      },
      writable: false,
    });
  }, BRANCH_ID);
}

async function routeEdgeReadModel(page: Page): Promise<void> {
  await page.route("**/api/v1/edge/read-model/**", async (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("branch_id")).toBe(BRANCH_ID);

    if (url.pathname.endsWith(`/catalog/${catalogItem.id}`)) {
      return fulfill(route, {
        metadata,
        item: {
          ...catalogItem,
          barcodes: [{ id: "barcode-1", code: "1234567890123", code_type: "ean13" }],
        },
      });
    }
    if (url.pathname.endsWith("/catalog")) {
      return fulfill(route, { metadata, items: [catalogItem], total: 1, page: 1, page_size: 50 });
    }
    if (url.pathname.endsWith("/suppliers")) {
      return fulfill(route, {
        metadata,
        items: [
          {
            id: "40000000-0000-4000-8000-000000000001",
            tenant_id: "10000000-0000-4000-8000-000000000001",
            name: "Поставщик Edge",
            legal_name: null,
            inn_or_tin: "123456789",
            contact_person: "Контакт",
            phone: "+992900000000",
            email: null,
            address: null,
            notes: null,
            is_active: true,
            created_at: "2026-09-20T09:00:00Z",
            updated_at: "2026-09-20T09:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      });
    }
    if (url.pathname.endsWith("/inventory")) {
      return fulfill(route, {
        metadata,
        items: [
          {
            id: "50000000-0000-4000-8000-000000000001",
            tenant_id: "10000000-0000-4000-8000-000000000001",
            branch_id: BRANCH_ID,
            catalog_id: catalogItem.id,
            batch_number: "EDGE-001",
            manufactured_at: "2026-01-01",
            expires_at: "2027-09-20",
            purchase_price: null,
            sale_price: "5.00",
            currency: "TJS",
            qty_initial: "20",
            qty_remaining: "12",
            is_blocked: false,
            block_reason: null,
            blocked_at: null,
            created_at: "2026-09-20T09:00:00Z",
            updated_at: "2026-09-20T09:00:00Z",
            branch_name: "Аптека Edge",
            catalog_name: catalogItem.brand_name,
            catalog_form: catalogItem.form,
            catalog_dosage: catalogItem.dosage,
            catalog_pack_size: catalogItem.pack_size,
            expiry_status: "normal",
            days_to_expiry: 365,
          },
        ],
        total: 1,
        page: 1,
        page_size: 50,
        summary: {
          total_qty: "12",
          purchase_value: null,
          sale_value: "60.00",
          attention_count: 0,
          expired_count: 0,
          blocked_count: 0,
        },
      });
    }

    return route.fulfill({ status: 404, body: "Not found" });
  });
}

function fulfill(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    contentType: "application/json",
    status: 200,
    body: JSON.stringify(body),
  });
}
