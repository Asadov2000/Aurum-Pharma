import { request, test, type APIRequestContext, type Page } from "@playwright/test";

import { type PostedElectronicRefund } from "../src/features/sales/electronicRefundPosting";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  completeSyntheticElectronicSale,
  type SyntheticElectronicSale,
} from "./electronicPhysicalFixture";
import {
  apiContext,
  apiLogin,
  clearLoginRateLimit,
  expect,
  loginInBrowser,
  OWNER,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

interface PaymentSettings {
  version: number;
  pos_payment_methods: string[];
  pos_mixed_payment_enabled: boolean;
  refund_reason_mode: string;
}

async function receivePhysicalStock(api: APIRequestContext, branchId: string, catalogId: string) {
  const supplier = await seedSupplier(api, uniqueName("Electronic refund supplier"));
  const created = await api.post("incoming", {
    data: {
      operation_id: crypto.randomUUID(),
      branch_id: branchId,
      supplier_id: supplier.id,
      document_date: new Date().toISOString().slice(0, 10),
    },
  });
  expect(created.ok()).toBe(true);
  const document = (await created.json()) as { id: string };
  const expiry = new Date();
  expiry.setUTCDate(expiry.getUTCDate() + 180);
  const added = await api.post(`incoming/${document.id}/items`, {
    data: {
      operation_id: crypto.randomUUID(),
      catalog_id: catalogId,
      expires_at: expiry.toISOString().slice(0, 10),
      qty: "4",
      purchase_price: "5.00",
      sale_price: "10.00",
    },
  });
  expect(added.ok()).toBe(true);
  const item = (await added.json()) as { id: string };
  expect(
    (
      await api.patch(`incoming/${document.id}/items/${item.id}`, {
        data: { prepare_unit_sale: true },
      })
    ).ok(),
  ).toBe(true);
  const checked = await api.get(`incoming/${document.id}`);
  expect(checked.ok()).toBe(true);
  const preview = (await checked.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${document.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: preview.acceptance_revision },
      })
    ).ok(),
  ).toBe(true);
}

async function openRefund(page: Page, sale: SyntheticElectronicSale): Promise<void> {
  await page.goto("/sales");
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      url.pathname === "/api/v1/sales" &&
      url.searchParams.get("receipt_number") === sale.receipt_number
    );
  });
  await page.getByLabel("№ чека").fill(sale.receipt_number);
  expect((await filtered).ok()).toBe(true);
  await page
    .getByRole("button", { name: `Открыть чек № ${sale.receipt_number}`, exact: true })
    .first()
    .click();
  await page
    .getByRole("dialog", { name: `Чек № ${sale.receipt_number}`, exact: true })
    .getByRole("button", { name: "Оформить возврат", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "Электронный физический возврат" })).toBeVisible();
}

function persistedCounts(parentId: string, refundId: string, operationId: string): unknown {
  for (const value of [parentId, refundId, operationId])
    expect(value).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  const sql = `SELECT json_build_object(
    'postings', (SELECT count(*) FROM pos_unit_electronic_refund_posting WHERE parent_sale_id='${parentId}'),
    'refunds', (SELECT count(*) FROM sale WHERE parent_sale_id='${parentId}' AND sale_type='return'),
    'payments', (SELECT count(*) FROM sale_payment WHERE sale_id='${refundId}'),
    'components', (SELECT count(*) FROM pos_unit_refund_payment_component WHERE electronic_refund_id='${operationId}')
  );`;
  return JSON.parse(
    dockerExec(E2E_POSTGRES_CONTAINER, [
      "psql",
      "-U",
      "postgres",
      "-d",
      E2E_POSTGRES_DB,
      "-At",
      "-c",
      sql,
    ]).trim(),
  ) as unknown;
}

for (const mode of ["card", "qr", "split"] as const) {
  test(`electronic physical ${mode} refund recovers lost posting after reload and shift closure`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    clearLoginRateLimit(OWNER.email);
    const anonymous = await request.newContext();
    const tokens = await apiLogin(anonymous, OWNER);
    const api = await apiContext(tokens.access_token);
    const originalResponse = await api.get("tenant/settings");
    expect(originalResponse.ok()).toBe(true);
    const settings = (await originalResponse.json()) as PaymentSettings;
    try {
      expect(
        (
          await api.patch("tenant/settings", {
            data: {
              expected_version: settings.version,
              pos_payment_methods: ["cash", "card", "qr"],
              pos_mixed_payment_enabled: true,
              refund_reason_mode: "required_with_text",
            },
          })
        ).ok(),
      ).toBe(true);
      const branch = await seedBranch(api, uniqueName("Electronic refund branch"));
      const register = await seedRegister(api, branch.id, uniqueName("Electronic refund register"));
      const medicine = await seedCatalogItem(api, uniqueName("Electronic unit medicine"), "10.00");
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
      await receivePhysicalStock(api, branch.id, medicine.id);
      expect(
        (
          await api.post("shifts/open", {
            data: { register_id: register.id, opening_cash: "0.00" },
          })
        ).ok(),
      ).toBe(true);
      const draftResponse = await api.post("sales", {
        data: { operation_id: crypto.randomUUID(), register_id: register.id },
      });
      expect(draftResponse.ok()).toBe(true);
      const draft = (await draftResponse.json()) as { id: string };
      expect(
        (
          await api.put(`sales/${draft.id}/physical-cart/items/${crypto.randomUUID()}`, {
            data: { catalog_id: medicine.id, position: 1, sale_format: "unit", quantity: 4 },
          })
        ).ok(),
      ).toBe(true);
      const meResponse = await api.get("auth/me");
      expect(meResponse.ok()).toBe(true);
      const me = (await meResponse.json()) as { id: string; active_tenant_id: string };
      const sale = completeSyntheticElectronicSale({
        tenantId: me.active_tenant_id,
        actorId: me.id,
        branchId: branch.id,
        saleId: draft.id,
        mode,
      });
      expect(sale.sale_id).toBe(draft.id);
      await page.setViewportSize({ width: 1366, height: 900 });
      await loginInBrowser(page, OWNER);
      await openRefund(page, sale);
      const modal = page.getByRole("dialog", { name: "Электронный физический возврат" });
      await modal.getByRole("checkbox").check();
      await modal.getByLabel(`Количество: ${medicine.brand_name}`, { exact: true }).fill("1");
      await modal
        .getByRole("button", { name: "Рассчитать электронный возврат", exact: true })
        .click();
      await expect(modal.getByText("Расчёт сервера: 0.50 TJS", { exact: true })).toBeVisible();
      await modal.getByRole("button", { name: "Сохранить выбранный расчёт", exact: true }).click();
      await modal.getByRole("button", { name: "Закрепить расчёт", exact: true }).click();
      await modal.getByRole("button", { name: "Активировать в своей смене", exact: true }).click();
      await expect(modal.getByText(/Это не ответ банка/)).toBeVisible();
      const terminalFields = modal.getByRole("textbox", { name: /^Терминал / });
      const count = mode === "split" ? 2 : 1;
      await expect(terminalFields).toHaveCount(count);
      for (let i = 0; i < count; i++) {
        // Submit each original card/QR component independently; cash has no evidence form.
        await terminalFields.first().fill("E2E-REFUND");
        await modal
          .getByRole("textbox", { name: /^Документ / })
          .first()
          .fill(`refund-${crypto.randomUUID()}`);
        await modal
          .getByRole("button", { name: "Сохранить ручные реквизиты", exact: true })
          .first()
          .click();
        await expect(terminalFields).toHaveCount(count - i - 1);
      }
      await modal.getByRole("combobox", { name: /Причина/ }).selectOption("quality_issue");
      await modal.getByLabel("Пояснение", { exact: true }).fill("Синтетическая проверка возврата");
      const commands: string[] = [];
      page.on("request", (req) => {
        if (
          req.method() === "POST" &&
          req.url().includes(`/sales/${sale.sale_id}/unit-electronic-refunds/`)
        )
          commands.push(new URL(req.url()).pathname);
      });
      let posted: PostedElectronicRefund | null = null;
      await page.route(
        `**/api/v1/sales/${sale.sale_id}/unit-electronic-refunds/reviews/*/post`,
        async (route) => {
          const response = await route.fetch();
          expect(response.ok()).toBe(true);
          posted = (await response.json()) as PostedElectronicRefund;
          await route.abort("failed");
        },
      );
      await modal.getByRole("button", { name: "Провести возврат", exact: true }).click();
      await expect.poll(() => posted !== null).toBe(true);
      await expect(modal.getByRole("alert")).toBeVisible();
      const saved = posted as PostedElectronicRefund | null;
      if (!saved) throw new Error("Saved posting result was not observed.");
      expect(saved).toMatchObject({
        state: "posted_refund",
        parent_sale_id: sale.sale_id,
        total_amount: "0.50",
      });
      expect(saved.receipt.payments.map((p) => p.method).sort()).toEqual(
        mode === "split" ? ["card", "cash", "qr"] : [mode],
      );
      const recoveryEntries = () =>
        page.evaluate(() =>
          Object.entries(localStorage).filter(([key]) =>
            key.startsWith("sales:electronicRefund:v1:"),
          ),
        );
      await expect.poll(async () => (await recoveryEntries()).length).toBe(1);
      const pending = (await recoveryEntries())[0]?.[1];
      expect(pending).toBeDefined();
      expect(JSON.parse(pending ?? "null")).toMatchObject({
        attempted: "post",
        request: { operation_id: saved.operation_id },
      });
      expect(pending).not.toContain("E2E-REFUND");
      expect(pending).not.toContain("Синтетическая проверка");
      const cashRefund = saved.receipt.payments.find((p) => p.method === "cash")?.amount ?? "0.00";
      const remaining = (mode === "split" ? 50n : 0n) - BigInt(cashRefund.replace(".", ""));
      const closingCash = `${remaining / 100n}.${String(remaining % 100n).padStart(2, "0")}`;
      expect(
        (
          await api.post(`shifts/${sale.shift_id}/close`, {
            data: { closing_cash_actual: closingCash },
          })
        ).ok(),
      ).toBe(true);
      const facts = persistedCounts(sale.sale_id, saved.sale_id, saved.operation_id);
      expect(facts).toEqual({
        postings: 1,
        refunds: 1,
        payments: mode === "split" ? 3 : 1,
        components: mode === "split" ? 3 : 1,
      });
      await page.reload();
      await openRefund(page, sale);
      await expect(
        modal.getByText("Возврат проведён. Сохранённый чек проверен.", { exact: true }),
      ).toBeVisible();
      expect((await recoveryEntries())[0]?.[1]).toBe(pending);
      expect(commands).toHaveLength(1);
      expect(commands[0]).toMatch(/\/post$/);
      await modal
        .getByRole("button", { name: "Открыть сохранённый чек возврата", exact: true })
        .click();
      await expect.poll(async () => (await recoveryEntries()).length).toBe(0);
      await page.getByRole("button", { name: "Печать возврата", exact: true }).click();
      await expect(
        page.getByRole("dialog", { name: "Печать возврата", exact: true }),
      ).toBeVisible();
      await expect(
        page.locator(".receipt-print").getByText(medicine.brand_name, { exact: false }),
      ).toBeVisible();
      expect(commands).toHaveLength(1);
      expect(persistedCounts(sale.sale_id, saved.sale_id, saved.operation_id)).toEqual(facts);
    } finally {
      const currentResponse = await api.get("tenant/settings");
      expect(currentResponse.ok()).toBe(true);
      const current = (await currentResponse.json()) as PaymentSettings;
      expect(
        (
          await api.patch("tenant/settings", {
            data: {
              expected_version: current.version,
              pos_payment_methods: settings.pos_payment_methods,
              pos_mixed_payment_enabled: settings.pos_mixed_payment_enabled,
              refund_reason_mode: settings.refund_reason_mode,
            },
          })
        ).ok(),
      ).toBe(true);
      await api.dispose();
      await anonymous.dispose();
    }
  });
}
