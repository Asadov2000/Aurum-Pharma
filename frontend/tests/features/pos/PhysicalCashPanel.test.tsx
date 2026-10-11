import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalCashPanel } from "@/features/pos/PhysicalCashPanel";
import { type PhysicalCartItem } from "@/features/pos/physicalCart";
import { type PhysicalCashCheckoutResult } from "@/features/pos/physicalCashCheckout";
import { type PendingPhysicalCash } from "@/features/pos/physicalCashRecovery";
import { type SaleItem } from "@/features/pos/types";

const api = vi.hoisted(() => ({
  quote: vi.fn(),
  parse: vi.fn(),
  load: vi.fn(),
  status: vi.fn(),
  run: vi.fn(),
  ack: vi.fn(),
}));
const mixedApi = vi.hoisted(() => ({
  parse: vi.fn(),
  load: vi.fn(),
  status: vi.fn(),
  run: vi.fn(),
  ack: vi.fn(),
}));
vi.mock("@/features/pos/mixedCashCheckout", () => ({
  getMixedCashOperation: mixedApi.status,
  parseMixedCashQuote: mixedApi.parse,
}));
vi.mock("@/features/pos/mixedCashRecovery", () => ({
  loadPendingMixedCash: mixedApi.load,
  runMixedCash: mixedApi.run,
  acknowledgeMixedCash: mixedApi.ack,
}));
vi.mock("@/features/pos/physicalCart", () => ({ getPhysicalCartQuote: api.quote }));
vi.mock("@/features/pos/physicalCashCheckout", () => ({
  getPhysicalCashOperation: api.status,
  parsePhysicalCashQuote: api.parse,
}));
vi.mock("@/features/pos/physicalCashRecovery", () => ({
  loadPendingPhysicalCash: api.load,
  runPhysicalCash: api.run,
  acknowledgePhysicalCash: api.ack,
}));

const uuid = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = { tenantId: uuid(1), userId: uuid(2) };
const saleId = uuid(3);
const branchId = uuid(4);
const registerId = uuid(5);
const packaging = {
  version: 2 as const,
  unit: "tablet" as const,
  units_per_pack: 30,
  units_per_blister: 10,
  dispense_step: 1,
};
const item: PhysicalCartItem = {
  id: uuid(6),
  tenant_id: scope.tenantId,
  branch_id: branchId,
  sale_id: saleId,
  catalog_id: uuid(7),
  position: 1,
  sale_format: "unit",
  quantity: 3,
  discount_amount: "0.00",
  physical_packaging: packaging,
};
const line = {
  inventory_kind: "physical_unit" as const,
  id: item.id,
  position: 1,
  catalog_id: item.catalog_id,
  packaging,
  sale_format: "unit" as const,
  quantity: 3,
  base_units: 3,
  original_retail_unit_price: "1.25",
  gross_amount: "3.75",
  discount_amount: "0.00",
  net_amount: "3.75",
  money_version: 1 as const,
  currency: "TJS" as const,
};
const quote = {
  revision_hash: "a".repeat(64),
  quote: {
    schema_version: 2 as const,
    state: "draft_quote" as const,
    tenant_id: scope.tenantId,
    cashier_id: scope.userId,
    sale_id: saleId,
    branch_id: branchId,
    register_id: registerId,
    shift_id: uuid(8),
    lines: [line],
    total_amount: "3.75",
    currency: "TJS" as const,
  },
};
const pending: PendingPhysicalCash = {
  version: 1,
  ...scope,
  createdAt: 100,
  request: {
    schema_version: 2,
    operation_id: uuid(9),
    sale_id: saleId,
    expected_revision_hash: quote.revision_hash,
    expected_total_amount: "3.75",
    cash_tendered: "5.00",
    currency: "TJS",
  },
};
const result: PhysicalCashCheckoutResult = {
  schema_version: 3,
  document_kind: "physical_checkout",
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: saleId,
  branch_id: branchId,
  register_id: registerId,
  shift_id: uuid(8),
  operation_id: uuid(9),
  event_id: uuid(10),
  stock_command_id: uuid(11),
  created_at: "2026-10-08T10:00:00Z",
  completed_at: "2026-10-08T10:00:01Z",
  receipt_number: "1",
  receipt_sequence: 1,
  is_test: false,
  lines: [{ ...line, name: "Лекарство" }],
  payments: [{ id: uuid(12), method: "cash", amount: "3.75", currency: "TJS" }],
  total_amount: "3.75",
  currency: "TJS",
};
const legacyItem: SaleItem = {
  id: uuid(13),
  sale_id: saleId,
  catalog_id: uuid(14),
  batch_id: uuid(15),
  qty: "1",
  unit_price: "10.00",
  total_price: "10.00",
  discount_amount: "0",
  currency: "TJS",
  position: 5,
};
const mixedQuote = {
  ...quote,
  quote: {
    ...quote.quote,
    lines: [
      {
        inventory_kind: "legacy_pack" as const,
        id: legacyItem.id,
        catalog_id: legacyItem.catalog_id,
        batch_id: legacyItem.batch_id,
        position: 1,
        quantity: "1.000",
        original_retail_unit_price: "10.00",
        gross_amount: "10.00",
        discount_amount: "0.00",
        net_amount: "10.00",
        money_version: 1 as const,
        currency: "TJS" as const,
      },
      { ...line, position: 2 },
    ],
    total_amount: "13.75",
  },
};
const mixedPending = {
  ...pending,
  request: { ...pending.request, expected_total_amount: "13.75", cash_tendered: "20.00" },
};
const mixedResult = {
  ...result,
  schema_version: 4 as const,
  document_kind: "mixed_checkout" as const,
  lines: mixedQuote.quote.lines.map((value) => ({ ...value, name: "Лекарство" })),
  payments: [{ ...result.payments[0]!, amount: "13.75" }],
  total_amount: "13.75",
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

describe("PhysicalCashPanel", () => {
  let active: boolean;
  const completed = vi.fn<() => Promise<boolean>>();
  const blocked = vi.fn<(value: boolean) => void>();
  const props = () => ({
    saleId,
    branchId,
    registerId,
    scope,
    items: [item],
    online: true,
    canPay: true,
    sessionIsActive: () => active,
    onBlockedChange: blocked,
    onCompleted: completed,
  });
  beforeEach(() => {
    vi.resetAllMocks();
    active = true;
    api.load.mockReturnValue(null);
    mixedApi.load.mockReturnValue(null);
    api.quote.mockResolvedValue(quote);
    api.parse.mockResolvedValue(quote);
    api.status.mockResolvedValue({ state: "saved_draft" });
    api.run.mockImplementation(() => {
      api.load.mockReturnValue(pending);
      return Promise.resolve(result);
    });
    api.ack.mockImplementation(() => {
      api.load.mockReturnValue(null);
      return Promise.resolve(true);
    });
    completed.mockResolvedValue(true);
  });
  afterEach(cleanup);
  async function fill() {
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Получено наличными, TJS"), {
      target: { value: "5,00" },
    });
  }
  it("calculates exact change and accepts the displayed result before acknowledgment", async () => {
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    expect(screen.getByText("1.25 TJS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    await waitFor(() => expect(api.ack).toHaveBeenCalledWith(saleId, result.operation_id, scope));
    expect(api.run).toHaveBeenCalledWith(saleId, scope, {
      schema_version: 2,
      sale_id: saleId,
      expected_revision_hash: quote.revision_hash,
      expected_total_amount: "3.75",
      cash_tendered: "5.00",
      currency: "TJS",
    });
    expect(completed).toHaveBeenCalledWith(result, true);
    expect(completed.mock.invocationCallOrder[0]).toBeLessThan(api.ack.mock.invocationCallOrder[0]);
  });
  it("locks immediately and prevents a double click from creating a second operation", async () => {
    const waiting = deferred<PhysicalCashCheckoutResult>();
    api.run.mockReturnValue(waiting.promise);
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    const button = screen.getByRole("button", { name: "Завершить продажу" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
    expect(blocked).toHaveBeenLastCalledWith(true);
  });
  it("rejects insufficient cash before sending any request", async () => {
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    fireEvent.change(screen.getByLabelText("Получено наличными, TJS"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    expect(await screen.findByText("Полученная сумма меньше суммы чека.")).toBeInTheDocument();
    expect(api.run).not.toHaveBeenCalled();
  });
  it("restores a completed operation after reload with a closed shift without submitting cash", async () => {
    api.load.mockReturnValue(pending);
    api.status.mockResolvedValue({ state: "completed", result });
    render(<PhysicalCashPanel {...props()} canPay={false} items={[]} />);
    await waitFor(() => expect(api.ack).toHaveBeenCalledOnce());
    expect(api.quote).not.toHaveBeenCalled();
    expect(api.run).not.toHaveBeenCalled();
    expect(completed).toHaveBeenCalledWith(result, false);
  });
  it("reads a saved operation on mount and requires explicit continuation of that same operation", async () => {
    api.load.mockReturnValue(pending);
    render(<PhysicalCashPanel {...props()} canPay={false} items={[]} />);
    await waitFor(() => expect(api.status).toHaveBeenCalledOnce());
    expect(api.run).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Проверить и завершить оплату" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Проверить и завершить оплату" }));
    await waitFor(() => expect(api.run).toHaveBeenCalledWith(saleId, scope, undefined));
    expect(completed).toHaveBeenCalledWith(result, false);
  });
  it("keeps an uncertain operation and cart locked after a lost response", async () => {
    api.run.mockImplementation(() => {
      api.load.mockReturnValue(pending);
      throw new Error("Связь прервалась");
    });
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    expect(await screen.findByText("Связь прервалась")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Проверить и завершить оплату" }),
    ).toBeInTheDocument();
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(api.ack).not.toHaveBeenCalled();
  });
  it("does not acknowledge a late response after an account change", async () => {
    const waiting = deferred<PhysicalCashCheckoutResult>();
    api.run.mockReturnValue(waiting.promise);
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    await waitFor(() => expect(api.run).toHaveBeenCalledOnce());
    active = false;
    await act(async () => {
      waiting.resolve(result);
    });
    expect(completed).not.toHaveBeenCalled();
    expect(api.ack).not.toHaveBeenCalled();
  });
  it("blocks manual recovery while mount reconciliation is still opening the receipt", async () => {
    api.load.mockReturnValue(pending);
    api.status.mockResolvedValue({ state: "completed", result });
    const displayed = deferred<boolean>();
    completed.mockReturnValue(displayed.promise);
    render(<PhysicalCashPanel {...props()} canPay={false} />);
    await waitFor(() => expect(completed).toHaveBeenCalledOnce());
    const button = screen.getByRole("button", { name: "Проверить и завершить оплату" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(api.run).not.toHaveBeenCalled();
    await act(async () => {
      displayed.resolve(true);
    });
    expect(completed).toHaveBeenCalledOnce();
    expect(api.ack).toHaveBeenCalledOnce();
  });
  it("does not clear storage after the account changes while opening a completed receipt", async () => {
    api.load.mockReturnValue(pending);
    api.status.mockResolvedValue({ state: "completed", result });
    const displayed = deferred<boolean>();
    completed.mockReturnValue(displayed.promise);
    render(<PhysicalCashPanel {...props()} canPay={false} />);
    await waitFor(() => expect(completed).toHaveBeenCalledOnce());
    active = false;
    await act(async () => {
      displayed.resolve(true);
    });
    expect(api.ack).not.toHaveBeenCalled();
    expect(api.load()).toEqual(pending);
  });
  it("retains recovery when opening the completed receipt fails", async () => {
    completed.mockResolvedValue(false);
    render(<PhysicalCashPanel {...props()} />);
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    expect(await screen.findByText(/Не удалось открыть чек/)).toBeInTheDocument();
    expect(api.ack).not.toHaveBeenCalled();
    expect(blocked).toHaveBeenLastCalledWith(true);
  });
  it("fails closed when saved payment storage is unavailable", () => {
    api.load.mockImplementation(() => {
      throw new Error("Хранилище недоступно");
    });
    render(<PhysicalCashPanel {...props()} />);
    expect(screen.getByText("Хранилище недоступно")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(api.quote).not.toHaveBeenCalled();
    expect(api.run).not.toHaveBeenCalled();
  });
  it("rejects a quote whose quantities differ from the visible cart", async () => {
    api.parse.mockResolvedValue({
      ...quote,
      quote: { ...quote.quote, lines: [{ ...line, quantity: 4 }] },
    });
    render(<PhysicalCashPanel {...props()} />);
    expect(
      await screen.findByText("Корзина изменилась. Обновите сумму перед оплатой."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();
  });
  it("pays both kinds through mixed cash using canonical positions and exact legacy decimals", async () => {
    mixedApi.parse.mockResolvedValue(mixedQuote);
    mixedApi.run.mockImplementation(() => {
      mixedApi.load.mockReturnValue(mixedPending);
      return Promise.resolve(mixedResult);
    });
    mixedApi.ack.mockImplementation(() => {
      mixedApi.load.mockReturnValue(null);
      return true;
    });
    const beforeStart = vi.fn().mockResolvedValue(undefined);
    render(
      <PhysicalCashPanel
        {...props()}
        kind="mixed"
        legacyItems={[legacyItem]}
        items={[{ ...item, position: 8 }]}
        beforeStart={beforeStart}
      />,
    );
    await fill();
    fireEvent.change(screen.getByLabelText("Получено наличными, TJS"), { target: { value: "20" } });
    expect(screen.getByText("6.25 TJS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    await waitFor(() => expect(mixedApi.ack).toHaveBeenCalledOnce());
    expect(mixedApi.run).toHaveBeenCalledWith(
      saleId,
      scope,
      expect.objectContaining({ expected_total_amount: "13.75", cash_tendered: "20.00" }),
    );
    expect(beforeStart.mock.invocationCallOrder[0]).toBeLessThan(
      mixedApi.run.mock.invocationCallOrder[0],
    );
    expect(completed).toHaveBeenCalledWith(mixedResult, true);
    expect(api.run).not.toHaveBeenCalled();
  });
  it("rejects changed legacy facts before a mixed payment", async () => {
    mixedApi.parse.mockResolvedValue(mixedQuote);
    render(
      <PhysicalCashPanel {...props()} kind="mixed" legacyItems={[{ ...legacyItem, qty: "2" }]} />,
    );
    expect(
      await screen.findByText("Корзина изменилась. Обновите сумму перед оплатой."),
    ).toBeInTheDocument();
    expect(mixedApi.run).not.toHaveBeenCalled();
  });
  it("recovers a mixed receipt with an empty completed cart and closed shift using GET only", async () => {
    mixedApi.load.mockReturnValue(mixedPending);
    mixedApi.status.mockResolvedValue({ state: "completed", result: mixedResult });
    mixedApi.ack.mockImplementation(() => {
      mixedApi.load.mockReturnValue(null);
      return true;
    });
    render(<PhysicalCashPanel {...props()} kind="mixed" items={[]} canPay={false} />);
    await waitFor(() => expect(mixedApi.ack).toHaveBeenCalledOnce());
    expect(completed).toHaveBeenCalledWith(mixedResult, false);
    expect(mixedApi.run).not.toHaveBeenCalled();
    expect(api.status).not.toHaveBeenCalled();
    expect(api.quote).not.toHaveBeenCalled();
  });
  it("releases editing without creating a financial operation when the prescription check fails", async () => {
    mixedApi.parse.mockResolvedValue(mixedQuote);
    render(
      <PhysicalCashPanel
        {...props()}
        kind="mixed"
        legacyItems={[legacyItem]}
        beforeStart={async () => {
          throw new Error("Сначала заполните рецепт.");
        }}
      />,
    );
    await fill();
    fireEvent.change(screen.getByLabelText("Получено наличными, TJS"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    expect(await screen.findByText("Сначала заполните рецепт.")).toBeInTheDocument();
    expect(mixedApi.run).not.toHaveBeenCalled();
    expect(blocked).toHaveBeenLastCalledWith(false);
  });
  it("rechecks checkout eligibility after the asynchronous prescription step", async () => {
    const preparation = deferred<void>();
    const beforeStart = vi.fn(() => preparation.promise);
    const view = render(<PhysicalCashPanel {...props()} beforeStart={beforeStart} />);
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    await waitFor(() => expect(beforeStart).toHaveBeenCalledOnce());
    view.rerender(<PhysicalCashPanel {...props()} canPay={false} beforeStart={beforeStart} />);
    await act(async () => preparation.resolve());
    expect(
      await screen.findByText(
        "Условия оплаты изменились. Проверьте смену и корзину перед оплатой.",
      ),
    ).toBeInTheDocument();
    expect(api.run).not.toHaveBeenCalled();
    expect(blocked).toHaveBeenLastCalledWith(false);
  });
  it("keeps editing blocked if another tab creates a pending payment during prescription checks", async () => {
    const preparation = deferred<void>();
    render(
      <PhysicalCashPanel
        {...props()}
        beforeStart={() =>
          preparation.promise.then(() => {
            api.load.mockReturnValue(pending);
            throw new Error("Проверка рецепта не завершена.");
          })
        }
      />,
    );
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    await act(async () => preparation.resolve());
    expect(await screen.findByText("Проверка рецепта не завершена.")).toBeInTheDocument();
    expect(api.run).not.toHaveBeenCalled();
    expect(blocked).toHaveBeenLastCalledWith(true);
  });
});
