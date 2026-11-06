import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalCartEditor, type PhysicalCartSelection } from "@/features/pos/PhysicalCartEditor";
import { type PhysicalCartItem, type PhysicalCartQuote } from "@/features/pos/physicalCart";
import {
  type PhysicalCartOptions,
  type PhysicalSaleFormat,
} from "@/features/pos/physicalCartOptions";
import {
  clearPhysicalCartRecovery,
  loadPhysicalCartRecovery,
  savePhysicalCartRecovery,
} from "@/features/pos/physicalCartRecovery";
import { type SaleItem } from "@/features/pos/types";
import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const api = vi.hoisted(() => ({ options: vi.fn(), quote: vi.fn(), put: vi.fn(), remove: vi.fn() }));
const pickerProps = vi.hoisted(() => vi.fn());
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
    branchId,
    showStock,
  }: {
    id: string;
    onChange: (value: string, name: string) => void;
    branchId?: string;
    showStock?: boolean;
  }) => {
    pickerProps({ branchId, showStock });
    return (
      <select id={id} defaultValue="" onChange={(event) => onChange(event.target.value, "Аспирин")}>
        <option value="">Выберите</option>
        <option value="10000000-0000-4000-8000-000000000001">Аспирин</option>
      </select>
    );
  },
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
          sale_format: "blister",
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
  function mount(
    items: PhysicalCartItem[] = [],
    disabled = false,
    initialFormat?: PhysicalSaleFormat,
    extra: {
      selection?: PhysicalCartSelection | null;
      onSelectionCancel?: () => void;
      onSelectionSaved?: () => void;
      onEditingChange?: (editing: boolean) => void;
      legacyItems?: readonly Pick<SaleItem, "id" | "sale_id" | "catalog_id" | "qty">[];
      onTotalChange?: (total: string | null) => void;
      hideTotal?: boolean;
    } = {},
  ) {
    const editor = (
      selection = extra.selection,
      currentItems = items,
      legacyItems = extra.legacyItems,
    ) => (
      <PhysicalCartEditor
        saleId="sale-a"
        branchId="branch-a"
        items={currentItems}
        nameById={{ [catalogId]: "Аспирин" }}
        disabled={disabled}
        sessionIsActive={() => active}
        initialRecovery={loadPhysicalCartRecovery("sale-a", POS_SCOPE)}
        initialFormat={initialFormat}
        onRecoveryChange={(operation) => {
          if (!active) return;
          if (operation) savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
          else clearPhysicalCartRecovery("sale-a", POS_SCOPE);
        }}
        onChanged={changed}
        onNameChange={nameChanged}
        onBusyChange={busy}
        {...extra}
        selection={selection}
        legacyItems={legacyItems}
      />
    );
    const view = render(editor());
    return {
      ...view,
      rerenderSelection: (selection: PhysicalCartSelection | null) =>
        view.rerender(editor(selection)),
      rerenderItems: (currentItems: PhysicalCartItem[], legacyItems = extra.legacyItems) =>
        view.rerender(editor(extra.selection, currentItems, legacyItems)),
    };
  }
  function selection(requestId = "selection-a", response = options()): PhysicalCartSelection {
    return { catalogId, name: "Аспирин", requestId, options: response };
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

  it("keeps catalog selection branch-scoped while hiding unrelated legacy stock", () => {
    mount();
    expect(pickerProps).toHaveBeenLastCalledWith({ branchId: "branch-a", showStock: false });
  });

  it.each([
    ["pack", "Упаковка", "Количество упаковок", 1],
    ["blister", "Блистер", "Количество блистеров", 2],
    ["unit", "Поштучно", "Количество, шт.", 10],
  ] as const)(
    "opens %s directly with the server step and retains it for the next entry",
    async (format, name, label, step) => {
      const response = options();
      response.options.find((option) => option.sale_format === format)!.quantity_step = step;
      api.options.mockResolvedValue(response);
      mount([], false, format);
      expect(screen.getByRole("radio", { name })).toHaveAttribute("aria-checked", "true");
      await select();
      expect(screen.getByLabelText(label)).toHaveValue(step);
      expect(api.put).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Добавить в корзину" }));
      await waitFor(() => expect(changed).toHaveBeenCalledOnce());
      expect(api.put).toHaveBeenCalledWith(
        "sale-a",
        expect.any(String),
        expect.objectContaining({
          sale_format: format,
          quantity: step,
        }),
      );
      await waitFor(() => expect(screen.getByLabelText(label)).toHaveValue(1));
      expect(screen.getByRole("radio", { name })).toHaveAttribute("aria-checked", "true");
    },
  );

  it.each(["1", "20"])(
    "keeps manually entered quantity %s when server options arrive",
    async (quantity) => {
      const pending = deferred<PhysicalCartOptions>();
      const response = options();
      response.options[2].quantity_step = 10;
      api.options.mockReturnValueOnce(pending.promise);
      mount([], false, "unit");
      fireEvent.change(screen.getByLabelText("Название лекарства"), {
        target: { value: catalogId },
      });
      const input = screen.getByLabelText("Количество, шт.");
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.change(input, { target: { value: quantity } });
      await act(async () => pending.resolve(response));
      expect(input).toHaveValue(Number(quantity));
      expect(screen.getByRole("radio", { name: "Поштучно" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
      expect(api.put).not.toHaveBeenCalled();
    },
  );

  it("does not replace an unavailable initial blister choice with a configured pack", async () => {
    const response = options();
    response.options[1] = {
      sale_format: "blister",
      configured: false,
      quantity_step: null,
      retail_unit_price: null,
      unavailable_reason: "unknown_blister",
    };
    api.options.mockResolvedValue(response);
    mount([], false, "blister");
    await select();
    expect(screen.getByRole("radio", { name: "Блистер" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Блистер" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить в корзину" })).toBeDisabled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("preserves recovered format and quantity ahead of the initial entry choice", async () => {
    const payload = {
      catalog_id: catalogId,
      sale_format: "blister" as const,
      quantity: 7,
      position: 1,
      discount_amount: "0.00",
    };
    savePhysicalCartRecovery("sale-a", POS_SCOPE, {
      kind: "save",
      itemId: item().id,
      name: "Аспирин",
      payload,
    });
    mount([], false, "unit");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeEnabled(),
    );
    expect(screen.getByRole("radio", { name: "Блистер" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("Количество блистеров")).toHaveValue(7);
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("sale-a", item().id, payload));
  });

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
    mount([item({ position: 4, discount_amount: "2.00" })], false, "unit");
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Блистер" })).toBeEnabled());
    expect(screen.getByRole("radio", { name: "Блистер" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("Количество блистеров")).toHaveValue(2);
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
  it("accepts external selection without a second search and requires an explicit sale unit", async () => {
    const saved = vi.fn();
    mount([], false, "pack", { selection: selection(), onSelectionSaved: saved });
    expect(screen.queryByLabelText("Название лекарства")).not.toBeInTheDocument();
    expect(pickerProps).not.toHaveBeenCalled();
    expect(api.options).not.toHaveBeenCalled();
    expect(screen.getByText("Аспирин")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Упаковки 30 шт." })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByLabelText("Количество")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("button", { name: "Добавить в чек" }).closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Выберите единицу продажи");
    expect(api.put).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("radio", { name: "Блистеры 10 шт." }));
    fireEvent.change(screen.getByLabelText("Количество блистеров"), { target: { value: "3" } });
    expect(screen.getByLabelText("Предварительная сумма")).toHaveTextContent(
      "3 × 10,00 = 30,00 сомони",
    );
    fireEvent.click(screen.getByRole("button", { name: "Добавить в чек" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(api.put).toHaveBeenCalledWith(
      "sale-a",
      expect.any(String),
      expect.objectContaining({ sale_format: "blister", quantity: 3 }),
    );
    expect(changed).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("Количество блистеров")).not.toBeInTheDocument();
  });

  it("keeps an empty external editor free of a second search and cancels without writing", () => {
    const cancel = vi.fn();
    const view = mount([], false, undefined, { selection: null, onSelectionCancel: cancel });
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Название лекарства")).not.toBeInTheDocument();
    view.rerenderSelection(selection());
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(cancel).toHaveBeenCalledOnce();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it.each(["sale_id", "branch_id", "catalog_id"] as const)(
    "rejects external options with wrong %s",
    async (field) => {
      const response = options();
      response[field] = "wrong-owner";
      mount([], false, undefined, { selection: selection("mismatched", response) });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Не удалось проверить параметры товара",
      );
      expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeDisabled();
      expect(api.options).not.toHaveBeenCalled();
      expect(api.put).not.toHaveBeenCalled();
    },
  );

  it("calculates a decimal preview exactly even above safe integer minor units", () => {
    const response = options();
    response.options[0].retail_unit_price = "999999999999.99";
    mount([], false, undefined, { selection: selection("large", response) });
    fireEvent.click(screen.getByRole("radio", { name: "Упаковки 30 шт." }));
    fireEvent.change(screen.getByLabelText("Количество упаковок"), { target: { value: "999999" } });
    expect(screen.getByLabelText("Предварительная сумма")).toHaveTextContent(
      "999999 × 999999999999,99 = 999998999999990000,01 сомони",
    );
    expect(api.put).not.toHaveBeenCalled();
  });

  it("resets the explicit unit for a repeated selection of the same medicine", () => {
    const view = mount([], false, undefined, { selection: selection() });
    fireEvent.click(screen.getByRole("radio", { name: "Штуки 1 шт." }));
    fireEvent.change(screen.getByLabelText("Количество, шт."), { target: { value: "4" } });
    view.rerenderSelection(selection("selection-b"));
    expect(screen.getByRole("radio", { name: "Штуки 1 шт." })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByLabelText("Количество")).toHaveValue(1);
    expect(screen.getByRole("button", { name: "Добавить в чек" })).toBeDisabled();
  });

  it("retains recovery priority and discards a selection received while recovering", async () => {
    const payload = {
      catalog_id: catalogId,
      sale_format: "blister" as const,
      quantity: 7,
      position: 4,
      discount_amount: "2.00",
    };
    savePhysicalCartRecovery("sale-a", POS_SCOPE, {
      kind: "save",
      itemId: item().id,
      name: "Восстановленный товар",
      payload,
    });
    const saved = vi.fn();
    mount([], false, undefined, { selection: selection(), onSelectionSaved: saved });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeEnabled(),
    );
    expect(screen.getByText("Восстановленный товар")).toBeInTheDocument();
    expect(screen.getByLabelText("Количество блистеров")).toHaveValue(7);
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put).toHaveBeenCalledWith("sale-a", item().id, payload);
    expect(saved).not.toHaveBeenCalled();
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("freezes an uncertain external save and does not replay a new selection after retry", async () => {
    const saved = vi.fn();
    api.put.mockRejectedValueOnce(new Error("uncertain save"));
    const view = mount([], false, undefined, { selection: selection(), onSelectionSaved: saved });
    fireEvent.click(screen.getByRole("radio", { name: "Штуки 1 шт." }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить в чек" }));
    await screen.findByText(/Не удалось подтвердить сохранение/);
    const original = api.put.mock.calls[0];
    view.rerenderSelection({ ...selection("ignored"), name: "Другой товар" });
    expect(screen.queryByText("Другой товар")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    expect(saved).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(api.put.mock.calls[1]).toEqual(original);
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("supports the existing integer keypad without writing until save", async () => {
    mount([], false, undefined, { selection: selection() });
    fireEvent.click(screen.getByRole("radio", { name: "Штуки 1 шт." }));
    fireEvent.click(screen.getByRole("button", { name: "Цифровой ввод" }));
    await screen.findByRole("dialog", { name: "Количество" });
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(screen.getByLabelText("Количество, шт.")).toHaveValue(7);
    expect(api.put).not.toHaveBeenCalled();
  });

  it("keeps line editing ahead of new external selections without replaying them", async () => {
    const view = mount([item({ position: 4, discount_amount: "2.00" })], false, undefined, {
      selection: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "Блистеры 10 шт." })).toBeEnabled(),
    );
    view.rerenderSelection({ ...selection("ignored-while-editing"), name: "Другой товар" });
    expect(screen.getByLabelText("Количество блистеров")).toHaveValue(2);
    expect(screen.queryByText("Другой товар")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить строку" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить строку" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put).toHaveBeenCalledWith(
      "sale-a",
      item().id,
      expect.objectContaining({
        position: 4,
        discount_amount: "2.00",
        quantity: 2,
        sale_format: "blister",
      }),
    );
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("does not notify external save completion after the session ends", async () => {
    const saved = vi.fn();
    const pending = deferred<PhysicalCartItem>();
    api.put.mockReturnValue(pending.promise);
    mount([], false, undefined, { selection: selection(), onSelectionSaved: saved });
    fireEvent.click(screen.getByRole("radio", { name: "Штуки 1 шт." }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить в чек" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledOnce());
    active = false;
    await act(async () => pending.resolve(item({ id: api.put.mock.calls[0][1] as string })));
    expect(saved).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });
  it.each(["cancel", "save", "unmount"])("reports an existing line edit until %s", async (end) => {
    const editingChanged = vi.fn();
    const view = mount([item()], false, undefined, {
      selection: null,
      onEditingChange: editingChanged,
    });
    expect(editingChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    expect(editingChanged).toHaveBeenCalledExactlyOnceWith(true);
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "Блистеры 10 шт." })).toBeEnabled(),
    );
    if (end === "cancel") fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    else if (end === "unmount") view.unmount();
    else {
      const pending = deferred<PhysicalCartItem>();
      api.put.mockReturnValue(pending.promise);
      fireEvent.click(screen.getByRole("button", { name: "Сохранить строку" }));
      await waitFor(() => expect(api.put).toHaveBeenCalledOnce());
      expect(editingChanged.mock.calls).toEqual([[true]]);
      await act(async () => pending.resolve(item()));
      await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    }
    expect(editingChanged.mock.calls).toEqual([[true], [false]]);
    if (end !== "unmount") view.unmount();
    expect(editingChanged.mock.calls).toEqual([[true], [false]]);
  });

  it("does not report a new external selection as editing an existing line", () => {
    const editingChanged = vi.fn();
    const view = mount([], false, undefined, {
      selection: selection(),
      onEditingChange: editingChanged,
    });
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    view.unmount();
    expect(editingChanged).not.toHaveBeenCalled();
  });
  it("shows external rows without a nested panel or idle add heading and publishes the server total", async () => {
    const totalChanged = vi.fn();
    const view = mount([item()], false, undefined, {
      selection: null,
      hideTotal: true,
      onTotalChange: totalChanged,
    });
    expect(screen.queryByRole("heading", { name: "Добавление товара" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Продажа упаковками, блистерами и поштучно" }),
    ).not.toHaveClass("border");
    expect(screen.getByText("Аспирин")).toBeInTheDocument();
    expect(totalChanged).toHaveBeenCalledWith(null);
    await waitFor(() => expect(totalChanged).toHaveBeenLastCalledWith("20.00"));
    const row = within(screen.getByRole("listitem"));
    expect(row.getByText("Блистер · 2")).toBeInTheDocument();
    expect(row.getByText("20,00 сомони", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText(/^Итого:/)).not.toBeInTheDocument();
    view.unmount();
    expect(totalChanged).toHaveBeenLastCalledWith(null);
  });

  it.each([
    ["sale_id", "wrong-sale"],
    ["branch_id", "wrong-branch"],
    ["tenant_id", "wrong-tenant"],
    ["currency", "USD"],
    ["total_amount", "NaN"],
  ])("does not publish a quote with invalid %s", async (field, value) => {
    const response = quote();
    Object.assign(response.quote, { [field]: value });
    api.quote.mockResolvedValue(response);
    const totalChanged = vi.fn();
    mount([item()], false, undefined, { selection: null, onTotalChange: totalChanged });
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
    expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
  });

  it.each(["id", "catalog", "quantity", "duplicate", "missing"])(
    "rejects physical quote rows with %s mismatch",
    async (mismatch) => {
      const response = quote();
      const line = response.quote.lines[0];
      if (mismatch === "id") line.id = "other-item";
      if (mismatch === "catalog") line.catalog_id = "other-catalog";
      if (mismatch === "quantity") line.quantity = 3;
      if (mismatch === "duplicate") response.quote.lines.push({ ...line });
      if (mismatch === "missing") response.quote.lines = [];
      api.quote.mockResolvedValue(response);
      const totalChanged = vi.fn();
      mount([item()], false, undefined, { selection: null, onTotalChange: totalChanged });
      expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
      expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
    },
  );

  it.each([
    ["1.000", true],
    ["1.001", false],
    ["1e0", false],
    ["1.0000", false],
    [1, false],
  ] as const)(
    "validates the server mixed-quote quantity %s without floating conversion",
    async (quantity, valid) => {
      const legacy = {
        id: "legacy-a",
        sale_id: "sale-a",
        catalog_id: "legacy-catalog",
        qty: "1.000",
      };
      const response = quote();
      response.quote.total_amount = "11.00";
      response.quote.lines[0] = {
        ...response.quote.lines[0],
        sale_format: "unit",
        original_retail_unit_price: "0.50",
        gross_amount: "1.00",
        net_amount: "1.00",
      };
      response.quote.lines.push({
        inventory_kind: "legacy_pack",
        id: legacy.id,
        catalog_id: legacy.catalog_id,
        position: 2,
        quantity,
        original_retail_unit_price: "10.00",
        gross_amount: "10.00",
        discount_amount: "0.00",
        net_amount: "10.00",
      });
      api.quote.mockResolvedValue(response);
      const totalChanged = vi.fn();
      mount([item({ sale_format: "unit" })], false, undefined, {
        selection: null,
        legacyItems: [legacy],
        onTotalChange: totalChanged,
      });
      if (valid) {
        await waitFor(() => expect(totalChanged).toHaveBeenLastCalledWith("11.00"));
        expect(screen.getByText("Итого: 11,00 сомони", { exact: true })).toBeInTheDocument();
        expect(screen.queryByText(/Не удалось рассчитать корзину/)).not.toBeInTheDocument();
      } else {
        expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
        expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
      }
    },
  );

  it("refreshes a mixed quote when only legacy quantity changes and clears the published total while loading", async () => {
    const legacy = {
      id: "legacy-a",
      sale_id: "sale-a",
      catalog_id: "legacy-catalog",
      qty: "1.500",
    };
    const response = quote();
    response.quote.total_amount = "23.00";
    response.quote.lines.push({
      ...response.quote.lines[0],
      inventory_kind: "legacy_pack",
      id: legacy.id,
      catalog_id: legacy.catalog_id,
      quantity: "1.500",
      gross_amount: "3.00",
      net_amount: "3.00",
    });
    const updated = structuredClone(response);
    updated.quote.total_amount = "24.00";
    updated.quote.lines[1].quantity = "2.000";
    updated.quote.lines[1].net_amount = "4.00";
    const pending = deferred<PhysicalCartQuote>();
    api.quote.mockResolvedValueOnce(response).mockReturnValueOnce(pending.promise);
    const totalChanged = vi.fn();
    const prepared = [item()];
    const view = mount(prepared, false, undefined, {
      selection: null,
      legacyItems: [legacy],
      onTotalChange: totalChanged,
    });
    await waitFor(() => expect(totalChanged).toHaveBeenLastCalledWith("23.00"));
    view.rerenderItems(prepared, [{ ...legacy, qty: "2.000" }]);
    expect(totalChanged).toHaveBeenLastCalledWith(null);
    await waitFor(() => expect(api.quote).toHaveBeenCalledTimes(2));
    await act(async () => pending.resolve(updated));
    expect(totalChanged).toHaveBeenLastCalledWith("24.00");
  });

  it("rejects stale legacy rows instead of publishing a mixed total", async () => {
    const legacy = {
      id: "legacy-a",
      sale_id: "sale-a",
      catalog_id: "legacy-catalog",
      qty: "2.000",
    };
    const response = quote();
    response.quote.lines.push({
      ...response.quote.lines[0],
      inventory_kind: "legacy_pack",
      id: legacy.id,
      catalog_id: legacy.catalog_id,
      quantity: "1.000",
    });
    api.quote.mockResolvedValue(response);
    const totalChanged = vi.fn();
    mount([item()], false, undefined, {
      selection: null,
      legacyItems: [legacy],
      onTotalChange: totalChanged,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
    expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
  });

  it("discards a late quote after the physical snapshot changes and clears the total when rows disappear", async () => {
    const first = deferred<PhysicalCartQuote>();
    const second = deferred<PhysicalCartQuote>();
    api.quote.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const totalChanged = vi.fn();
    const view = mount([item()], false, undefined, {
      selection: null,
      onTotalChange: totalChanged,
    });
    const oldSignal = api.quote.mock.calls[0][1] as AbortSignal;
    view.rerenderItems([item({ quantity: 3 })]);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => first.resolve(quote()));
    expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
    const fresh = quote();
    fresh.quote.lines[0].quantity = 3;
    fresh.quote.total_amount = "30.00";
    await act(async () => second.resolve(fresh));
    expect(totalChanged).toHaveBeenLastCalledWith("30.00");
    view.rerenderItems([]);
    expect(totalChanged).toHaveBeenLastCalledWith(null);
  });

  it("clears a previously published total when a refreshed quote fails", async () => {
    const totalChanged = vi.fn();
    const view = mount([item()], false, undefined, {
      selection: null,
      onTotalChange: totalChanged,
    });
    await waitFor(() => expect(totalChanged).toHaveBeenLastCalledWith("20.00"));
    api.quote.mockRejectedValueOnce(new Error("offline"));
    view.rerenderItems([item({ quantity: 3 })]);
    expect(totalChanged).toHaveBeenLastCalledWith(null);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
    expect(totalChanged).toHaveBeenLastCalledWith(null);
  });

  it("keeps existing rows locked until the external product selection is cancelled", async () => {
    const cancelled = vi.fn();
    const editingChanged = vi.fn();
    mount([item()], false, undefined, {
      selection: selection(),
      onSelectionCancel: cancelled,
      onEditingChange: editingChanged,
    });
    expect(screen.getByRole("button", { name: "Изменить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Убрать" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    fireEvent.click(screen.getByRole("button", { name: "Убрать" }));
    expect(editingChanged).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(cancelled).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Изменить" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    expect(editingChanged).toHaveBeenCalledExactlyOnceWith(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить строку" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(editingChanged.mock.calls).toEqual([[true], [false]]);
    expect(cancelled).toHaveBeenCalledOnce();
  });

  function mountStepper(quantity = 20, step = 10) {
    const response = options();
    response.options[2].quantity_step = step;
    api.options.mockResolvedValue(response);
    const serverQuote = quote();
    serverQuote.quote.lines[0].quantity = quantity;
    serverQuote.quote.lines[0].sale_format = "unit";
    serverQuote.quote.lines[0].discount_amount = "2.00";
    api.quote.mockResolvedValue(serverQuote);
    const row = item({ sale_format: "unit", quantity, position: 4, discount_amount: "2.00" });
    const view = mount([row], false, undefined, { selection: null });
    return { ...view, row };
  }

  it.each([
    ["Уменьшить количество", 10],
    ["Увеличить количество", 30],
  ])(
    "%s uses the server step and preserves line identity, discount and position",
    async (name, quantity) => {
      const { row } = mountStepper();
      const control = screen.getByRole("button", { name });
      expect(control).toBeDisabled();
      await waitFor(() => expect(control).toBeEnabled());
      fireEvent.click(control);
      await waitFor(() => expect(changed).toHaveBeenCalledOnce());
      expect(api.put).toHaveBeenCalledExactlyOnceWith("sale-a", row.id, {
        catalog_id: catalogId,
        sale_format: "unit",
        quantity,
        position: 4,
        discount_amount: "2.00",
      });
      expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
      expect(screen.queryByRole("button", { name: "Сохранить строку" })).not.toBeInTheDocument();
    },
  );

  it.each([
    [10, "Уменьшить количество", "Увеличить количество"],
    [1_000_000, "Увеличить количество", "Уменьшить количество"],
  ])(
    "disables the step boundary at quantity %i without silently clamping",
    async (quantity, blocked, allowed) => {
      mountStepper(quantity);
      await waitFor(() => expect(screen.getByRole("button", { name: allowed })).toBeEnabled());
      expect(screen.getByRole("button", { name: blocked })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: blocked }));
      expect(api.put).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Изменить" })).toHaveTextContent(String(quantity));
    },
  );

  it("blocks duplicate quick quantity writes until the first response is acknowledged", async () => {
    const pending = deferred<PhysicalCartItem>();
    api.put.mockReturnValue(pending.promise);
    const { row } = mountStepper();
    const control = screen.getByRole("button", { name: "Увеличить количество" });
    await waitFor(() => expect(control).toBeEnabled());
    fireEvent.click(control);
    fireEvent.click(control);
    expect(api.put).toHaveBeenCalledOnce();
    expect(control).toBeDisabled();
    expect(screen.getByRole("button", { name: "Уменьшить количество" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Убрать" })).toBeDisabled();
    expect(busy).toHaveBeenCalledWith(true);
    await act(async () => pending.resolve({ ...row, quantity: 30 }));
    expect(changed).toHaveBeenCalledOnce();
    expect(api.put).toHaveBeenCalledOnce();
    expect(busy).toHaveBeenLastCalledWith(false);
  });

  it("releases recovery after a definite quick-save rejection without changing the displayed row", async () => {
    api.put.mockRejectedValueOnce({ isAxiosError: true, response: { status: 422 } });
    mountStepper();
    const control = screen.getByRole("button", { name: "Увеличить количество" });
    await waitFor(() => expect(control).toBeEnabled());
    fireEvent.click(control);
    await screen.findByText(/Не удалось подтвердить сохранение/);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(busy).toHaveBeenLastCalledWith(false);
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Изменить" })).toHaveTextContent("20");
    expect(screen.getByRole("button", { name: "Отменить" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await waitFor(() => expect(control).toBeEnabled());
    expect(api.put).toHaveBeenCalledOnce();
  });

  it("retries an uncertain quick-save with the frozen UUID and payload", async () => {
    api.put.mockRejectedValueOnce(new Error("response lost"));
    const { row } = mountStepper();
    const control = screen.getByRole("button", { name: "Увеличить количество" });
    await waitFor(() => expect(control).toBeEnabled());
    fireEvent.click(control);
    await screen.findByText(/Не удалось подтвердить сохранение/);
    const original = api.put.mock.calls[0];
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toMatchObject({
      kind: "save",
      itemId: row.id,
      payload: { quantity: 30 },
    });
    expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
    expect(control).toBeDisabled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(api.put.mock.calls[1]).toEqual(original);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(busy).toHaveBeenLastCalledWith(false);
  });

  it.each(["sale_id", "branch_id", "catalog_id", "tenant_id", "currency"])(
    "blocks quick quantity changes with mismatched options %s",
    async (field) => {
      const response = options();
      Object.assign(response, { [field]: "wrong-owner" });
      api.options.mockResolvedValue(response);
      mount([item()], false, undefined, { selection: null });
      await screen.findByText(/Не удалось проверить шаг количества/);
      expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Уменьшить количество" })).toBeDisabled();
      expect(api.put).not.toHaveBeenCalled();
    },
  );

  it("retains quick-save recovery without applying a late response after the session ends", async () => {
    const pending = deferred<PhysicalCartItem>();
    api.put.mockReturnValue(pending.promise);
    const { row } = mountStepper();
    const control = screen.getByRole("button", { name: "Увеличить количество" });
    await waitFor(() => expect(control).toBeEnabled());
    fireEvent.click(control);
    expect(api.put).toHaveBeenCalledOnce();
    active = false;
    await act(async () => pending.resolve({ ...row, quantity: 30 }));
    expect(changed).not.toHaveBeenCalled();
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).not.toBeNull();
    expect(busy).toHaveBeenLastCalledWith(true);
  });

  it("reuses accepted selection options for its saved row without a second options request", async () => {
    const view = mount([], false, undefined, { selection: selection() });
    fireEvent.click(screen.getByRole("radio", { name: "Блистеры 10 шт." }));
    fireEvent.change(screen.getByLabelText("Количество блистеров"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить в чек" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    view.rerenderItems([item()]);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeEnabled(),
    );
    expect(api.options).not.toHaveBeenCalled();
  });

  it("does not reread a cached catalog when only row quantity changes", async () => {
    const { row, rerenderItems } = mountStepper();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeEnabled(),
    );
    expect(api.options).toHaveBeenCalledOnce();
    const fresh = quote();
    fresh.quote.lines[0].quantity = 30;
    fresh.quote.lines[0].sale_format = "unit";
    fresh.quote.lines[0].discount_amount = "2.00";
    api.quote.mockResolvedValue(fresh);
    rerenderItems([{ ...row, quantity: 30 }]);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeEnabled(),
    );
    expect(api.options).toHaveBeenCalledOnce();
  });

  it("reads missing catalog options sequentially instead of fanning out across the receipt", async () => {
    const nextCatalog = "10000000-0000-4000-8000-000000000002";
    const secondRow = item({
      id: "20000000-0000-4000-8000-000000000002",
      catalog_id: nextCatalog,
      position: 2,
    });
    const first = deferred<PhysicalCartOptions>();
    const second = deferred<PhysicalCartOptions>();
    api.options.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const response = quote();
    response.quote.lines.push({
      ...response.quote.lines[0],
      id: secondRow.id,
      catalog_id: nextCatalog,
    });
    response.quote.total_amount = "40.00";
    api.quote.mockResolvedValue(response);
    mount([item(), secondRow], false, undefined, { selection: null });
    expect(api.options).toHaveBeenCalledOnce();
    expect(api.options.mock.calls[0][1]).toBe(catalogId);
    await act(async () => first.resolve(options()));
    await waitFor(() => expect(api.options).toHaveBeenCalledTimes(2));
    expect(api.options.mock.calls[1][1]).toBe(nextCatalog);
    expect(screen.getAllByRole("button", { name: "Увеличить количество" })[0]).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Увеличить количество" })[1]).toBeDisabled();
    await act(async () => second.resolve({ ...options(), catalog_id: nextCatalog }));
    expect(screen.getAllByRole("button", { name: "Увеличить количество" })[1]).toBeEnabled();
    expect(api.options).toHaveBeenCalledTimes(2);
  });

  it("keeps explicit edit available to retry a failed row options lookup", async () => {
    api.options.mockRejectedValueOnce(new Error("offline"));
    mount([item()], false, undefined, { selection: null });
    await screen.findByText(/Не удалось проверить шаг количества/);
    expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить строку" })).toBeEnabled(),
    );
    expect(api.options).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(screen.getByRole("button", { name: "Увеличить количество" })).toBeEnabled();
    expect(api.put).not.toHaveBeenCalled();
  });
  it.each(["sale_format", "discount_amount"] as const)(
    "rejects a quote when the same row changes %s",
    async (field) => {
      const response = quote();
      response.quote.lines[0].quantity = 1;
      response.quote.lines[0].sale_format = "unit";
      if (field === "sale_format") response.quote.lines[0].sale_format = "pack";
      else response.quote.lines[0].discount_amount = "0.50";
      api.quote.mockResolvedValue(response);
      const totalChanged = vi.fn();
      mount([item({ sale_format: "unit", quantity: 1 })], false, undefined, {
        selection: null,
        onTotalChange: totalChanged,
      });
      expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось рассчитать корзину");
      expect(totalChanged.mock.calls.every(([total]) => total === null)).toBe(true);
      const row = within(screen.getByRole("listitem"));
      expect(row.getByText("Поштучно · 1")).toBeInTheDocument();
      expect(row.getByText("Не рассчитано", { exact: true })).toBeInTheDocument();
    },
  );
});
