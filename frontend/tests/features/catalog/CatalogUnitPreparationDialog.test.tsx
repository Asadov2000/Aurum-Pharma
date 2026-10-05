import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
const auth = vi.hoisted(() => ({
  permissions: ["catalog.view", "catalog.update"],
  updateScope: null as string[] | null,
}));

vi.mock("@/lib/api", () => ({ api: calls }));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "cashier-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      support_access: null,
      permissions: auth.permissions,
      permission_scopes: { "catalog.view": null, "catalog.update": auth.updateScope },
    },
  }),
}));

import { CatalogUnitPreparationDialog } from "@/features/catalog/CatalogUnitPreparationDialog";
import { catalogKeys } from "@/features/catalog/queries";

const PREPARATION = {
  version: 1,
  state: "prepared",
  unit: "capsule",
  units_per_pack: 30,
  dispense_step: 5,
  unit_price: "1.25",
};
const ITEM = {
  id: "item-1",
  tenant_id: "tenant-1",
  brand_name: "Тестовое лекарство",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: null,
  pack_size: "№20",
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "99.00",
  currency: "TJS",
  is_active: true,
  deleted_at: null,
  created_at: "2026-10-05T10:00:00Z",
  updated_at: "2026-10-05T10:00:00Z",
  barcodes: [],
  unit_sale_preparation: null,
};

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <CatalogUnitPreparationDialog itemId="item-1" onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

async function fillValid(price = "0,35") {
  await screen.findByLabelText("Единица отпуска");
  fireEvent.change(screen.getByLabelText("Единица отпуска"), { target: { value: "tablet" } });
  fireEvent.change(screen.getByLabelText("Сколько штук в упаковке?"), { target: { value: "20" } });
  fireEvent.change(screen.getByLabelText(/Цена одной единицы/), { target: { value: price } });
}

describe("CatalogUnitPreparationDialog", () => {
  beforeEach(() => {
    calls.get.mockReset();
    calls.patch.mockReset();
    calls.get.mockResolvedValue({ data: ITEM });
    calls.patch.mockResolvedValue({ data: ITEM });
    auth.permissions = ["catalog.view", "catalog.update"];
    auth.updateScope = null;
  });

  it("refreshes fresh cached data on first mount and reopening after save", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 60_000 },
        mutations: { retry: false },
      },
    });
    client.setQueryData(catalogKeys.item("item-1"), ITEM);
    calls.get.mockResolvedValue({ data: { ...ITEM, unit_sale_preparation: PREPARATION } });
    const onClose = vi.fn();
    const view = () => (
      <QueryClientProvider client={client}>
        <CatalogUnitPreparationDialog itemId="item-1" onClose={onClose} />
      </QueryClientProvider>
    );
    const first = render(view());
    await waitFor(() =>
      expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("30"),
    );
    expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("1.25");
    expect(calls.get).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
      signal: expect.any(AbortSignal),
    });
    const updated = { ...PREPARATION, dispense_step: 1, unit_price: "2.10" };
    calls.get.mockResolvedValue({ data: { ...ITEM, unit_sale_preparation: updated } });
    fireEvent.change(screen.getByLabelText(/Цена одной единицы/), { target: { value: "2.10" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(client.getQueryData(catalogKeys.item("item-1"))).toEqual({
        ...ITEM,
        unit_sale_preparation: updated,
      }),
    );
    first.unmount();
    const requestsBeforeReopen = calls.get.mock.calls.length;
    calls.get.mockResolvedValue({
      data: { ...ITEM, unit_sale_preparation: { ...updated, unit_price: "2.15" } },
    });
    const reopened = render(view());
    await waitFor(() => expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("2.15"));
    expect(calls.get.mock.calls.length).toBeGreaterThan(requestsBeforeReopen);
    expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("30");
    reopened.unmount();
    client.clear();
  });

  it("loads the item and saves only explicit preparation with a normalized comma price", async () => {
    const { onClose } = setup();
    await fillValid();
    expect(calls.get).toHaveBeenCalledWith("/catalog/item-1", { signal: expect.any(AbortSignal) });
    expect(screen.getByText(/TJS/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: {
          version: 1,
          state: "prepared",
          unit: "tablet",
          units_per_pack: 20,
          dispense_step: 1,
          units_per_blister: null,
          unit_price: "0.35",
        },
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("restores saved values and explicitly removes preparation with null", async () => {
    calls.get.mockResolvedValue({ data: { ...ITEM, unit_sale_preparation: PREPARATION } });
    const { onClose } = setup();
    await waitFor(() =>
      expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("30"),
    );
    expect(screen.getByLabelText("Единица отпуска")).toHaveValue("capsule");
    expect(screen.queryByLabelText("Сколько штук в одном блистере?")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("1.25");
    fireEvent.click(screen.getByRole("button", { name: "Убрать подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: null,
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it.each([
    ["2", "2", "0.01"],
    ["10000", "10000", "999999999999.99"],
  ])("preserves valid boundary values %s/%s/%s exactly", async (pack, blister, price) => {
    const { onClose } = setup();
    await fillValid(price);
    fireEvent.change(screen.getByLabelText("Сколько штук в упаковке?"), {
      target: { value: pack },
    });
    fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
    fireEvent.change(screen.getByLabelText("Сколько штук в одном блистере?"), {
      target: { value: blister },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: {
          version: 1,
          state: "prepared",
          unit: "tablet",
          units_per_pack: Number(pack),
          dispense_step: 1,
          units_per_blister: Number(blister),
          unit_price: price,
        },
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it.each([
    ["Сколько штук в упаковке?", "1"],
    ["Сколько штук в упаковке?", "10001"],
    ["Сколько штук в упаковке?", "20.5"],
    ["Цена одной единицы", "0"],
    ["Цена одной единицы", "-1"],
    ["Цена одной единицы", "0.001"],
    ["Цена одной единицы", "1000000000000.00"],
    ["Цена одной единицы", "NaN"],
  ])("rejects invalid %s=%s without a PATCH", async (label, value) => {
    const { onClose } = setup();
    await fillValid();
    fireEvent.change(screen.getByLabelText(label, { exact: false }), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
    expect(calls.patch).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not infer unit price or packaging from the ordinary catalog fields", async () => {
    setup();
    await screen.findByLabelText(/Цена одной единицы/);
    expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("");
    expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("");
    expect(calls.patch).not.toHaveBeenCalled();
  });

  it("shows only pack composition and requires an explicit blister size", async () => {
    setup();
    await screen.findByLabelText("Сколько штук в упаковке?");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Сколько штук в одном блистере?")).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "В пачке есть блистеры (плажки)" }),
    ).not.toBeChecked();
    fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
    expect(screen.getByRole("checkbox", { name: "В пачке есть блистеры (плажки)" })).toBeChecked();
    expect(screen.getByLabelText("Сколько штук в одном блистере?")).toHaveValue("");
    expect(screen.getByLabelText("Сколько штук в одном блистере?")).toHaveAttribute(
      "inputmode",
      "numeric",
    );
  });

  it("saves the number of units per blister independently of dispensing mode", async () => {
    setup();
    await fillValid();
    fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
    fireEvent.change(screen.getByLabelText("Сколько штук в одном блистере?"), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: {
          version: 1,
          state: "prepared",
          unit: "tablet",
          units_per_pack: 20,
          dispense_step: 1,
          units_per_blister: 10,
          unit_price: "0.35",
        },
      }),
    );
  });

  it.each(["", "1", "3", "21", "2.5", "10001", "true"])(
    "rejects invalid or missing blister size %s without a PATCH",
    async (value) => {
      const { onClose } = setup();
      await fillValid();
      fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
      fireEvent.change(screen.getByLabelText("Сколько штук в одном блистере?"), {
        target: { value },
      });
      fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
      expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
      expect(calls.patch).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    },
  );

  it("restores saved blister composition and clears it explicitly when switched off", async () => {
    const saved = { ...PREPARATION, dispense_step: 1, units_per_blister: 10 };
    calls.get.mockResolvedValue({ data: { ...ITEM, unit_sale_preparation: saved } });
    setup();
    const blister = await screen.findByLabelText("Сколько штук в одном блистере?");
    expect(blister).toHaveValue("10");
    expect(screen.getByRole("checkbox", { name: "В пачке есть блистеры (плажки)" })).toBeChecked();
    expect(calls.patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
    expect(screen.queryByLabelText("Сколько штук в одном блистере?")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: { ...saved, units_per_blister: null },
      }),
    );
  });

  it("warns about a legacy dispensing step and resets it only on explicit save", async () => {
    calls.get.mockResolvedValue({ data: { ...ITEM, unit_sale_preparation: PREPARATION } });
    setup();
    await screen.findByLabelText("Сколько штук в упаковке?");
    expect(screen.getByText(/После сохранения/)).toBeInTheDocument();
    expect(calls.patch).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Единица отпуска")).toHaveValue("capsule");
    expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("30");
    expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("1.25");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith("/catalog/item-1", {
        unit_sale_preparation: { ...PREPARATION, dispense_step: 1, units_per_blister: null },
      }),
    );
  });

  it("keeps the blister size after changing pack size and rejects incompatible composition", async () => {
    const { onClose } = setup();
    await fillValid();
    fireEvent.click(screen.getByText("В пачке есть блистеры (плажки)", { exact: true }));
    fireEvent.change(screen.getByLabelText("Сколько штук в одном блистере?"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("Сколько штук в упаковке?"), {
      target: { value: "21" },
    });
    expect(screen.getByLabelText("Сколько штук в одном блистере?")).toHaveValue("10");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    expect(
      (await screen.findAllByRole("alert")).some((alert) =>
        alert.textContent?.includes("целое число блистеров"),
      ),
    ).toBe(true);
    expect(calls.patch).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
  it("keeps entered values and the dialog open when PATCH fails", async () => {
    calls.patch.mockRejectedValue(new Error("request failed"));
    const { onClose } = setup();
    await fillValid("1.25");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Цена одной единицы/)).toHaveValue("1.25");
    expect(screen.getByLabelText("Сколько штук в упаковке?")).toHaveValue("20");
    calls.patch.mockResolvedValue({ data: ITEM });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledTimes(2);
  });

  it("shows a GET error without allowing a mutation", async () => {
    calls.get.mockRejectedValue(new Error("request failed"));
    const { onClose } = setup();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Цена одной единицы/)).not.toBeInTheDocument();
    expect(calls.patch).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it.each(["missing", "branch-only"])(
    "does not load or offer the form with %s update access",
    async (kind) => {
      if (kind === "missing") auth.permissions = ["catalog.view"];
      else auth.updateScope = ["branch-1"];
      setup();
      expect(screen.queryByLabelText("Единица отпуска")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Сохранить подготовку" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Убрать подготовку" })).not.toBeInTheDocument();
      expect(calls.get).not.toHaveBeenCalled();
      expect(calls.patch).not.toHaveBeenCalled();
      expect(screen.getByText(/прав|доступ/i)).toBeInTheDocument();
    },
  );

  it("cancels without changing the catalog", async () => {
    const { onClose } = setup();
    await fillValid();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(calls.patch).not.toHaveBeenCalled();
  });
});
