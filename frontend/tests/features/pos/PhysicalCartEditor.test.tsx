import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalCartEditor } from "@/features/pos/PhysicalCartEditor";
import { type PhysicalCartItem, type PhysicalCartQuote } from "@/features/pos/physicalCart";
import { type PhysicalCartOptions } from "@/features/pos/physicalCartOptions";
import {
  clearPhysicalCartRecovery,
  loadPhysicalCartRecovery,
  savePhysicalCartRecovery,
} from "@/features/pos/physicalCartRecovery";
import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const api = vi.hoisted(() => ({ options: vi.fn(), quote: vi.fn(), put: vi.fn(), remove: vi.fn() }));
vi.mock("@/features/pos/physicalCartOptions", () => ({ getPhysicalCartOptions: api.options }));
vi.mock("@/features/pos/physicalCart", () => ({
  getPhysicalCartQuote: api.quote,
  putPhysicalCartItem: api.put,
  deletePhysicalCartItem: api.remove,
}));
vi.mock("@/features/catalog/CatalogPicker", () => ({
  CatalogPicker: ({
    id,
    onChange,
  }: {
    id: string;
    onChange: (value: string, name: string) => void;
  }) => (
    <select id={id} defaultValue="" onChange={(event) => onChange(event.target.value, "Аспирин")}>
      <option value="">Выберите</option>
      <option value="10000000-0000-4000-8000-000000000001">Аспирин</option>
    </select>
  ),
}));

const catalogId = "10000000-0000-4000-8000-000000000001";
const packaging = {
  version: 2 as const,
  unit: "tablet" as const,
  units_per_pack: 30,
  units_per_blister: 10,
  dispense_step: 1,
};
function options(): PhysicalCartOptions {
  return {
    state: "physical_options_preview",
    tenant_id: "tenant-a",
    branch_id: "branch-a",
    sale_id: "sale-a",
    catalog_id: catalogId,
    stock_date: "2026-10-08",
    packaging,
    currency: "TJS",
    checkout_enabled: false,
    stock_reserved: false,
    stock: { base_units: 60, closed_packs: 2, intact_blisters: 6 },
    options: [
      {
        sale_format: "pack",
        configured: true,
        quantity_step: 1,
        retail_unit_price: "30.00",
        unavailable_reason: null,
      },
      {
        sale_format: "blister",
        configured: true,
        quantity_step: 1,
        retail_unit_price: "10.00",
        unavailable_reason: null,
      },
      {
        sale_format: "unit",
        configured: true,
        quantity_step: 1,
        retail_unit_price: "1.00",
        unavailable_reason: null,
      },
    ],
  };
}
function item(overrides: Partial<PhysicalCartItem> = {}): PhysicalCartItem {
  return {
    id: "20000000-0000-4000-8000-000000000001",
    tenant_id: "tenant-a",
    branch_id: "branch-a",
    sale_id: "sale-a",
    catalog_id: catalogId,
    position: 1,
    sale_format: "blister",
    quantity: 2,
    discount_amount: "0.00",
    physical_packaging: packaging,
    ...overrides,
  };
}
function quote(): PhysicalCartQuote {
  return {
    revision_hash: "revision-a",
    quote: {
      schema_version: 2,
      state: "draft_quote",
      tenant_id: "tenant-a",
      branch_id: "branch-a",
      sale_id: "sale-a",
      register_id: "register-a",
      shift_id: "shift-a",
      cashier_id: "user-a",
      currency: "TJS",
      total_amount: "20.00",
      lines: [
        {
          inventory_kind: "physical_unit",
          id: "20000000-0000-4000-8000-000000000001",
          catalog_id: catalogId,
          position: 1,
          quantity: 2,
          original_retail_unit_price: "10.00",
          gross_amount: "20.00",
          discount_amount: "0.00",
          net_amount: "20.00",
        },
      ],
    },
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("PhysicalCartEditor", () => {
  let active: boolean;
  const changed = vi.fn<() => Promise<void>>();
  const busy = vi.fn<(value: boolean) => void>();
  const nameChanged = vi.fn<(id: string, name: string) => void>();
  function mount(items: PhysicalCartItem[] = [], disabled = false) {
    return render(
      <PhysicalCartEditor
        saleId="sale-a"
        branchId="branch-a"
        items={items}
        nameById={{ [catalogId]: "Аспирин" }}
        disabled={disabled}
        sessionIsActive={() => active}
        initialRecovery={loadPhysicalCartRecovery("sale-a", POS_SCOPE)}
        onRecoveryChange={(operation) => {
          if (!active) return;
          if (operation) savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
          else clearPhysicalCartRecovery("sale-a", POS_SCOPE);
        }}
        onChanged={changed}
        onNameChange={nameChanged}
        onBusyChange={busy}
      />,
    );
  }
  async function select() {
    fireEvent.change(screen.getByLabelText("Название лекарства"), { target: { value: catalogId } });
    await waitFor(() => expect(screen.getByRole("radio", { name: "Упаковка" })).toBeEnabled());
  }
  beforeEach(() => {
    active = true;
    authenticatePos();
    window.localStorage.clear();
    vi.clearAllMocks();
    api.options.mockResolvedValue(options());
    api.quote.mockResolvedValue(quote());
    api.remove.mockResolvedValue(undefined);
    api.put.mockImplementation(
      async (_sale: string, id: string, payload: Partial<PhysicalCartItem>) =>
        item({ ...payload, id }),
    );
    changed.mockResolvedValue(undefined);
  });
  afterEach(cleanup);

  it.each([
    ["Упаковка", "pack", "Количество упаковок"],
    ["Блистер", "blister", "Количество блистеров"],
    ["Поштучно", "unit", "Количество, шт."],
  ])("saves %s as server intent without payment", async (name, format, label) => {
    mount();
    await select();
    fireEvent.click(screen.getByRole("radio", { name }));
    fireEvent.change(screen.getByLabelText(label), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put).toHaveBeenCalledWith("sale-a", expect.stringMatching(/^[\da-f-]{36}$/), {
      catalog_id: catalogId,
      sale_format: format,
      quantity: 2,
      position: 1,
      discount_amount: "0.00",
    });
    expect(api.remove).not.toHaveBeenCalled();
    expect(busy.mock.calls).toEqual([[true], [false]]);
  });

  it("preserves the same line UUID for an uncertain save retry", async () => {
    api.put.mockRejectedValueOnce(new Error("timeout"));
    mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await screen.findByText(/Не удалось подтвердить сохранение/);
    expect(nameChanged).not.toHaveBeenCalled();
    const firstId = api.put.mock.calls[0][1] as string;
    expect(screen.getByLabelText("Название лекарства")).toBeDisabled();
    expect(screen.getByLabelText("Количество упаковок")).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Блистер" })).toBeDisabled();
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toMatchObject({
      itemId: firstId,
      kind: "save",
    });
    expect(busy.mock.calls.at(-1)).toEqual([true]);
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put.mock.calls[1][1]).toBe(firstId);
    expect(api.put.mock.calls[1][2]).toEqual(api.put.mock.calls[0][2]);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(busy.mock.calls.at(-1)).toEqual([false]);
  });

  it("rejects quantity outside the configured dispense step", async () => {
    const response = options();
    response.options[2].quantity_step = 10;
    api.options.mockResolvedValue(response);
    mount();
    await select();
    fireEvent.click(screen.getByRole("radio", { name: "Поштучно" }));
    expect(screen.getByLabelText("Количество, шт.")).toHaveValue(10);
    fireEvent.change(screen.getByLabelText("Количество, шт."), { target: { value: "3" } });
    fireEvent.submit(screen.getByRole("button", { name: "Добавить в корзину" }).closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Количество должно быть кратно 10.");
    expect(api.put).not.toHaveBeenCalled();
  });

  it("keeps an unconfigured blister option unavailable", async () => {
    const response = options();
    response.options[1] = {
      sale_format: "blister",
      configured: false,
      quantity_step: null,
      retail_unit_price: null,
      unavailable_reason: "unknown_blister",
    };
    api.options.mockResolvedValue(response);
    mount();
    await select();
    expect(screen.getByRole("radio", { name: "Блистер" })).toBeDisabled();
  });

  it("restores item prices only from a matching server quote", async () => {
    mount([item()]);
    expect(screen.getByText("Итого: Не рассчитано")).toBeInTheDocument();
    expect(await screen.findByText("Итого: 20.00 TJS")).toBeInTheDocument();
    expect(screen.getByText("Блистер · 2 · 20.00 TJS")).toBeInTheDocument();
  });

  it.each(["failure", "wrong-branch"])(
    "does not fabricate a total after quote %s",
    async (reason) => {
      if (reason === "failure") api.quote.mockRejectedValue(new Error("offline"));
      else {
        const response = quote();
        response.quote.branch_id = "branch-b";
        api.quote.mockResolvedValue(response);
      }
      mount([item()]);
      expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
      expect(screen.getByText("Итого: Не рассчитано")).toBeInTheDocument();
    },
  );

  it("keeps prepared v1 lines read-only", async () => {
    mount([item({ physical_packaging: null })]);
    await screen.findByText("Итого: 20.00 TJS");
    expect(screen.getByRole("button", { name: "Изменить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Удалить" })).toBeDisabled();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
  });

  it("does not fetch or write while disabled", () => {
    mount([item()], true);
    fireEvent.change(screen.getByLabelText("Название лекарства"), { target: { value: catalogId } });
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(api.options).not.toHaveBeenCalled();
    expect(api.quote).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
  });

  it.each(["session", "unmount"])("ignores late options after %s ends", async (end) => {
    const pending = deferred<PhysicalCartOptions>();
    api.options.mockReturnValue(pending.promise);
    const view = mount();
    fireEvent.change(screen.getByLabelText("Название лекарства"), { target: { value: catalogId } });
    await waitFor(() => expect(api.options).toHaveBeenCalledOnce());
    const signal = api.options.mock.calls[0][2] as AbortSignal;
    if (end === "unmount") view.unmount();
    else active = false;
    await act(async () => pending.resolve(options()));
    if (end === "unmount") expect(signal.aborted).toBe(true);
    else expect(screen.getByRole("radio", { name: "Упаковка" })).toBeDisabled();
    expect(changed).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it.each(["save", "delete"])(
    "ignores late %s success after owner session ends",
    async (operation) => {
      const pending = deferred<PhysicalCartItem>();
      if (operation === "save") api.put.mockReturnValue(pending.promise);
      else api.remove.mockReturnValue(pending.promise);
      mount(operation === "delete" ? [item()] : []);
      if (operation === "save") {
        await select();
        fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
      } else fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
      await waitFor(() => expect(busy).toHaveBeenCalledWith(true));
      active = false;
      const savedId =
        operation === "save"
          ? (api.put.mock.calls[0][1] as string)
          : "20000000-0000-4000-8000-000000000001";
      await act(async () => pending.resolve(item({ id: savedId })));
      expect(changed).not.toHaveBeenCalled();
      expect(nameChanged).not.toHaveBeenCalled();
      expect(busy.mock.calls).toEqual([[true]]);
      expect(api.put).toHaveBeenCalledTimes(operation === "save" ? 1 : 0);
      expect(api.remove).toHaveBeenCalledTimes(operation === "delete" ? 1 : 0);
    },
  );

  it("releases only the mounted editor lock on unmount and ignores late save", async () => {
    const pending = deferred<PhysicalCartItem>();
    api.put.mockReturnValue(pending.promise);
    const view = mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledOnce());
    const id = api.put.mock.calls[0][1] as string;
    view.unmount();
    expect(busy.mock.calls).toEqual([[true], [false]]);
    await act(async () => pending.resolve(item({ id })));
    expect(changed).not.toHaveBeenCalled();
    expect(nameChanged).not.toHaveBeenCalled();
    expect(busy.mock.calls).toEqual([[true], [false]]);
  });

  it("updates the restored line identity and preserves its discount and position", async () => {
    mount([item({ position: 4, discount_amount: "2.00" })]);
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Блистер" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Количество блистеров"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить строку" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put).toHaveBeenCalledWith("sale-a", "20000000-0000-4000-8000-000000000001", {
      catalog_id: catalogId,
      sale_format: "blister",
      quantity: 3,
      position: 4,
      discount_amount: "2.00",
    });
  });

  it.each(["0", "-1", "1.5", "1000001"])(
    "rejects invalid whole quantity %s without a write",
    async (quantity) => {
      mount();
      await select();
      fireEvent.change(screen.getByLabelText("Количество упаковок"), {
        target: { value: quantity },
      });
      fireEvent.submit(screen.getByRole("button", { name: "Добавить в корзину" }).closest("form")!);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "целое количество от 1 до 1 000 000",
      );
      expect(api.put).not.toHaveBeenCalled();
      expect(changed).not.toHaveBeenCalled();
    },
  );

  it("does not acknowledge a save response belonging to another sale", async () => {
    api.put.mockImplementation(async (_sale: string, id: string) =>
      item({ id, sale_id: "sale-b" }),
    );
    mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Результат сохранения не подтверждён",
    );
    expect(changed).not.toHaveBeenCalled();
    expect(nameChanged).not.toHaveBeenCalled();
  });

  it("publishes the chosen medicine name only after server acknowledgement and before refetch", async () => {
    const pending = deferred<PhysicalCartItem>();
    api.put.mockReturnValue(pending.promise);
    mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledOnce());
    expect(nameChanged).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
    const id = api.put.mock.calls[0][1] as string;
    await act(async () => pending.resolve(item({ id })));
    expect(nameChanged).toHaveBeenCalledExactlyOnceWith(catalogId, "Аспирин");
    expect(changed).toHaveBeenCalledOnce();
    expect(nameChanged.mock.invocationCallOrder[0]).toBeLessThan(
      changed.mock.invocationCallOrder[0],
    );
  });

  it("retains the frozen acknowledged operation when refreshing the sale fails", async () => {
    changed.mockRejectedValueOnce(new Error("refresh offline"));
    mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await screen.findByText(/Не удалось подтвердить сохранение/);
    const original = api.put.mock.calls[0];
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toMatchObject({ itemId: original[1] });
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    expect(busy.mock.calls.at(-1)).toEqual([true]);
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
    expect(api.put.mock.calls[1]).toEqual(original);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(busy.mock.calls.at(-1)).toEqual([false]);
  });

  it("restores an uncertain save after unmount and retries the persisted item UUID", async () => {
    api.put.mockRejectedValueOnce(new Error("uncertain response"));
    const view = mount();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    await screen.findByText(/Не удалось подтвердить сохранение/);
    const original = api.put.mock.calls[0];
    view.unmount();
    mount();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Количество упаковок")).toHaveValue(1);
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put.mock.calls[1]).toEqual(original);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
  });

  it("keeps an uncertain delete locked and accepts a missing replay only after authorized refresh", async () => {
    api.remove.mockRejectedValueOnce(new Error("delete timeout"));
    mount([item()]);
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await screen.findByText(/Не удалось подтвердить удаление/);
    expect(screen.getByRole("button", { name: "Изменить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Удалить" })).toBeDisabled();
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toMatchObject({
      kind: "delete",
      itemId: item().id,
    });
    api.remove.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } });
    changed.mockRejectedValueOnce(new Error("refresh forbidden"));
    fireEvent.click(screen.getByRole("button", { name: "Повторить удаление" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).not.toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить удаление" })).toBeEnabled(),
    );
    api.remove.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } });
    fireEvent.click(screen.getByRole("button", { name: "Повторить удаление" }));
    await waitFor(() => expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull());
    expect(api.remove.mock.calls.every((call) => call[1] === item().id)).toBe(true);
    expect(busy.mock.calls.at(-1)).toEqual([false]);
  });

  it.each(["save", "delete"])("ignores late %s failure after session ends", async (operation) => {
    const pending = deferred<PhysicalCartItem>();
    if (operation === "save") api.put.mockReturnValue(pending.promise);
    else api.remove.mockReturnValue(pending.promise);
    mount(operation === "delete" ? [item()] : []);
    if (operation === "save") {
      await select();
      fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
    } else fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(busy).toHaveBeenCalledWith(true));
    active = false;
    await act(async () => pending.reject(new Error("late offline")));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
    expect(busy.mock.calls).toEqual([[true]]);
  });
});
