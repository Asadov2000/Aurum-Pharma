import { type APIRequestContext } from "@playwright/test";

import { dockerExec, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import {
  expect,
  seedBranch,
  seedCatalogItem,
  seedRegister,
  seedSupplier,
  uniqueName,
} from "./helpers";

export interface RehearsalSale {
  sale_id: string;
  operation_id: string;
  receipt_number: string;
  shift_id: string;
  completed_at: string;
  total_amount: string;
  lines: { sale_format: string; quantity: number; base_units: number }[];
}

export interface RehearsalRefund {
  state: string;
  operation_id: string;
  parent_sale_id: string;
  sale_id: string;
  total_amount: string;
  receipt: { receipt_number: string; completed_at: string };
}

export interface RehearsalFixture {
  branch: { id: string; name: string };
  register: { id: string; name: string };
  medicine: { id: string; brand_name: string };
  lotId: string;
}

export async function readRehearsalLot(api: APIRequestContext, fixture: RehearsalFixture) {
  const response = await api.get("batches/physical", {
    params: { branch_id: fixture.branch.id, show_empty: true },
  });
  expect(response.ok()).toBe(true);
  const result = (await response.json()) as {
    total: number;
    items: { lot_id: string; base_units: string; closed_packs: string; opened_units: string }[];
  };
  expect(result.total).toBe(1);
  const lot = result.items[0];
  if (!lot) throw new Error("The isolated rehearsal lot is missing");
  return lot;
}

export async function seedRehearsal(api: APIRequestContext): Promise<RehearsalFixture> {
  const branch = await seedBranch(api, uniqueName("Учебная репетиция — вымышленные данные"));
  const register = await seedRegister(api, branch.id, uniqueName("Учебная касса"));
  const medicine = await seedCatalogItem(
    api,
    uniqueName("УЧЕБНЫЕ ТАБЛЕТКИ — НЕ ДЛЯ ОТПУСКА"),
    "30.00",
  );
  const configured = await api.patch(`catalog/${medicine.id}`, {
    data: {
      dispensing_type: "otc",
      unit_sale_preparation: {
        version: 1,
        state: "prepared",
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
        unit_price: "1.00",
      },
    },
  });
  expect(configured.ok()).toBe(true);
  const supplier = await seedSupplier(api, uniqueName("Вымышленный учебный поставщик"));
  const created = await api.post("incoming", {
    data: {
      operation_id: crypto.randomUUID(),
      branch_id: branch.id,
      supplier_id: supplier.id,
      document_date: new Date().toISOString().slice(0, 10),
    },
  });
  expect(created.ok()).toBe(true);
  const incoming = (await created.json()) as { id: string };
  const expiry = new Date();
  expiry.setUTCFullYear(expiry.getUTCFullYear() + 2);
  const added = await api.post(`incoming/${incoming.id}/items`, {
    data: {
      operation_id: crypto.randomUUID(),
      catalog_id: medicine.id,
      qty: "2",
      purchase_price: "18.00",
      sale_price: "30.00",
      expires_at: expiry.toISOString().slice(0, 10),
    },
  });
  expect(added.ok()).toBe(true);
  const item = (await added.json()) as { id: string };
  expect(
    (
      await api.patch(`incoming/${incoming.id}/items/${item.id}`, {
        data: { prepare_unit_sale: true },
      })
    ).ok(),
  ).toBe(true);
  const checked = await api.get(`incoming/${incoming.id}`);
  expect(checked.ok()).toBe(true);
  const preview = (await checked.json()) as { acceptance_revision: string };
  expect(
    (
      await api.post(`incoming/${incoming.id}/accept`, {
        data: { stock_mode: "physical_unit_v2", expected_revision: preview.acceptance_revision },
      })
    ).ok(),
  ).toBe(true);
  const fixture = { branch, register, medicine, lotId: "" };
  const lot = await readRehearsalLot(api, fixture);
  expect(lot).toMatchObject({ base_units: "60", closed_packs: "2", opened_units: "0" });
  fixture.lotId = lot.lot_id;
  return fixture;
}

export interface RehearsalFacts {
  sales: number;
  returns: number;
  payments: number;
  checkouts: number;
  remaining: number;
  quarantine: number;
  writeoffs: number;
  writtenOff: number;
  net: string;
  movements: number;
  stockDigest: string;
  completedDigest: string;
}

export function rehearsalFacts(fixture: RehearsalFixture): RehearsalFacts {
  const { lotId, register, branch } = fixture;
  for (const id of [lotId, register.id, branch.id]) {
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  }
  // dockerExec verifies immutable container IDs and matching disposable Compose labels.
  // SELECT returns only synthetic counts, quantities and digests: no receipt or cost data.
  const sql = `SELECT json_build_object(
    'sales',(SELECT count(*) FROM sale WHERE register_id='${register.id}' AND status='completed' AND sale_type='sale'),
    'returns',(SELECT count(*) FROM sale WHERE register_id='${register.id}' AND status='completed' AND sale_type='return'),
    'payments',(SELECT count(*) FROM sale_payment p JOIN sale s ON s.id=p.sale_id AND s.tenant_id=p.tenant_id WHERE s.register_id='${register.id}'),
    'checkouts',(SELECT count(*) FROM pos_physical_checkout c JOIN sale s ON s.id=c.sale_id AND s.tenant_id=c.tenant_id WHERE s.register_id='${register.id}'),
    'remaining',(SELECT l.closed_packs*30+COALESCE((SELECT sum(p.remaining_units) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id AND p.tenant_id=l.tenant_id),0) FROM unit_stock_lot_v2 l WHERE l.id='${lotId}'),
    'quarantine',(SELECT COALESCE(sum(l.base_units),0) FROM pos_physical_return_line l JOIN pos_physical_return_command c ON c.id=l.command_id AND c.tenant_id=l.tenant_id WHERE c.branch_id='${branch.id}'),
    'writeoffs',(SELECT count(*) FROM physical_writeoff_command WHERE lot_id='${lotId}'),
    'writtenOff',(SELECT COALESCE(sum(base_units),0) FROM physical_writeoff_command WHERE lot_id='${lotId}'),
    'net',(SELECT COALESCE(sum(CASE WHEN s.sale_type='return' THEN -p.amount ELSE p.amount END),0)::TEXT FROM sale_payment p JOIN sale s ON s.id=p.sale_id AND s.tenant_id=p.tenant_id WHERE s.register_id='${register.id}'),
    'movements',(SELECT count(*) FROM unit_stock_movement_v2 WHERE lot_id='${lotId}'),
    'stockDigest',(SELECT md5(concat(l.closed_packs,'|',(SELECT string_agg(concat(p.id,':',p.remaining_units,':',p.state::TEXT),',' ORDER BY p.id) FROM unit_stock_opened_pack_v2 p WHERE p.lot_id=l.id AND p.tenant_id=l.tenant_id))) FROM unit_stock_lot_v2 l WHERE l.id='${lotId}'),
    'completedDigest',(SELECT md5(string_agg(concat(s.id,':',s.status,':',s.total_amount,':',s.operation_id,':',s.completed_at),',' ORDER BY s.id)) FROM sale s WHERE s.register_id='${register.id}' AND s.status='completed' AND s.sale_type='sale')
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
  ) as RehearsalFacts;
}

export async function verifySavedOperations(
  api: APIRequestContext,
  sales: RehearsalSale[],
  refund: RehearsalRefund,
): Promise<void> {
  for (const sale of sales) {
    const response = await api.get(
      `sales/${sale.sale_id}/physical-cash/operations/${sale.operation_id}`,
    );
    expect(response.ok()).toBe(true);
    const stored = (await response.json()) as { state: string; result: RehearsalSale };
    expect(stored.state).toBe("completed");
    // Compare as a boolean so a failed assertion cannot dump a complete receipt.
    expect(JSON.stringify(stored.result) === JSON.stringify(sale)).toBe(true);
  }
  const response = await api.get(
    `sales/${refund.parent_sale_id}/physical-cash-refunds/operations/${refund.operation_id}`,
  );
  expect(response.ok()).toBe(true);
  expect(JSON.stringify(await response.json()) === JSON.stringify(refund)).toBe(true);
}

export async function verifyPendingQuarantine(
  api: APIRequestContext,
  fixture: RehearsalFixture,
  refund: RehearsalRefund,
): Promise<void> {
  const response = await api.get("customer-returns/physical", {
    params: { branch_id: fixture.branch.id, search: fixture.medicine.brand_name },
  });
  expect(response.ok()).toBe(true);
  const queue = (await response.json()) as {
    total: number;
    items: {
      lot_id: string;
      base_units: number;
      status: string;
      intake_kind: string;
      parent_sale_id: string;
      return_sale_id: string;
      operation_id: string;
    }[];
  };
  expect(queue.total).toBe(1);
  expect(queue.items).toHaveLength(1);
  const item = queue.items[0];
  if (!item) throw new Error("The pending rehearsal quarantine entry is missing");
  expect(item).toMatchObject({ base_units: 1, status: "pending", intake_kind: "cash_refund" });
  expect(
    item.lot_id === fixture.lotId &&
      item.parent_sale_id === refund.parent_sale_id &&
      item.return_sale_id === refund.sale_id &&
      item.operation_id === refund.operation_id,
  ).toBe(true);
}

export async function verifyRehearsalReports(
  api: APIRequestContext,
  fixture: RehearsalFixture,
  sales: RehearsalSale[],
  refund: RehearsalRefund,
): Promise<void> {
  const firstSale = sales[0];
  if (!firstSale) throw new Error("The rehearsal requires completed sales");
  const settings = await api.get("tenant/operational-settings");
  expect(settings.ok()).toBe(true);
  const { report_timezone: timeZone } = (await settings.json()) as { report_timezone: string };
  const days = [...sales.map((sale) => sale.completed_at), refund.receipt.completed_at]
    .map((date) => {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).formatToParts(new Date(date));
      return ["year", "month", "day"]
        .map((type) => parts.find((part) => part.type === type)?.value)
        .join("-");
    })
    .sort();
  const from = days[0];
  const to = days[days.length - 1];
  if (!from || !to) throw new Error("The rehearsal report range is missing");
  const response = await api.get("reports/sales-summary", {
    params: { from, to, branch_id: fixture.branch.id },
  });
  expect(response.ok()).toBe(true);
  expect(await response.json()).toMatchObject({
    gross_sales: "43.00",
    total_discounts: "0.00",
    total_refunds: "1.00",
    net: "42.00",
    sales_count: 3,
    returns_count: 1,
  });
  const report = await api.get(`shifts/${firstSale.shift_id}/z-report`);
  expect(report.ok()).toBe(true);
  expect(await report.json()).toMatchObject({
    closing_cash_expected: "142.00",
    closing_difference: "0.00",
    sales_count: 3,
    returns_count: 1,
    totals: { sales_total: "43.00", discounts_total: "0.00", returns_total: "1.00" },
  });
}
