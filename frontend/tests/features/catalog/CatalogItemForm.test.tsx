import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }));
const auth = vi.hoisted(() => ({
  permissions: ["catalog.update"],
  scope: null as string[] | null,
}));
vi.mock("@/lib/api", () => ({ api: calls }));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      support_access: null,
      permissions: auth.permissions,
      permission_scopes: { "catalog.update": auth.scope },
    },
  }),
}));
vi.mock("@/features/catalog/fieldTemplates", () => ({
  useFieldTemplates: () => ({ data: [], isLoading: false, isError: false }),
  useCreateFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { CatalogItemForm } from "@/features/catalog/CatalogItemForm";
import type { CatalogItem } from "@/features/catalog/types";

const ITEM: CatalogItem = {
  id: "item-1",
  tenant_id: "tenant-1",
  brand_name: "Тестовое лекарство",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: null,
  pack_size: "№20 старое описание",
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: "99.00",
  currency: "TJS",
  is_active: true,
  created_at: "2026-10-05T10:00:00Z",
  updated_at: "2026-10-05T10:00:00Z",
  unit_sale_preparation: {
    version: 1,
    state: "prepared",
    unit: "capsule",
    units_per_pack: 30,
    dispense_step: 1,
    units_per_blister: 10,
    unit_price: "1.25",
  },
};

function setup(item: CatalogItem | null = ITEM) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const onCreated = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <CatalogItemForm item={item} onClose={onClose} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { onClose, onCreated };
}

const ENABLE_UNITS = "Настроить поштучную продажу";
const HAS_BLISTER = "В пачке есть блистеры (плажки)";
const UNPREPARED: CatalogItem = { ...ITEM, unit_sale_preparation: null };

function fillComposition(price = "0,35") {
  fireEvent.click(screen.getByText(ENABLE_UNITS, { exact: true }));
  fireEvent.change(screen.getByLabelText("Количество в упаковке"), { target: { value: "20" } });
  fireEvent.change(screen.getByLabelText("Что в упаковке"), { target: { value: "tablet" } });
  fireEvent.change(screen.getByLabelText("Цена одной штуки, TJS"), { target: { value: price } });
}

function save() {
  fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения", exact: true }));
}

describe("CatalogItemForm packaging", () => {
  beforeEach(() => {
    calls.get.mockReset();
    calls.patch.mockReset();
    calls.post.mockReset();
    calls.patch.mockResolvedValue({ data: ITEM });
    calls.post.mockResolvedValue({ data: ITEM });
    auth.permissions = ["catalog.update"];
    auth.scope = null;
  });

  it("shows saved composition in the single form and omits untouched preparation from an ordinary save", async () => {
    const { onClose } = setup();
    expect(screen.getByRole("checkbox", { name: ENABLE_UNITS })).toBeChecked();
    expect(
      screen.queryByRole("textbox", { name: "Размер упаковки", exact: true }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Количество в упаковке")).toHaveValue("30");
    expect(screen.getByLabelText("Что в упаковке")).toHaveValue("capsule");
    expect(screen.getByLabelText("Штук в одном блистере")).toHaveValue("10");
    expect(screen.getByLabelText("Цена одной штуки, TJS")).toHaveValue("1.25");
    expect(screen.queryByRole("button", { name: "Сохранить подготовку" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Другое название" },
    });
    save();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledTimes(1);
    expect(calls.patch.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ brand_name: "Другое название" }),
    );
    expect(calls.patch.mock.calls[0]?.[1]).not.toHaveProperty("pack_size");
    expect(calls.patch.mock.calls[0]?.[1]).not.toHaveProperty("unit_sale_preparation");
    expect(calls.get).not.toHaveBeenCalled();
  });

  it("does not parse the old free text or save composition before the common Save button", async () => {
    const { onClose } = setup(UNPREPARED);
    expect(screen.getByLabelText("Размер упаковки")).toHaveValue(ITEM.pack_size);
    fireEvent.click(screen.getByText(ENABLE_UNITS, { exact: true }));
    expect(
      screen.queryByRole("textbox", { name: "Размер упаковки", exact: true }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Количество в упаковке")).toHaveValue("");
    expect(screen.getByLabelText("Цена одной штуки, TJS")).toHaveValue("");
    expect(screen.getByRole("checkbox", { name: HAS_BLISTER })).not.toBeChecked();
    expect(screen.queryByLabelText("Штук в одном блистере")).not.toBeInTheDocument();
    expect(calls.patch).not.toHaveBeenCalled();
    expect(calls.get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(calls.patch).not.toHaveBeenCalled();
  });

  it("saves regular fields, canonical packaging and preparation in one PATCH", async () => {
    const { onClose } = setup(UNPREPARED);
    fillComposition();
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Новое название" },
    });
    save();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledExactlyOnceWith(
      "/catalog/item-1",
      expect.objectContaining({
        brand_name: "Новое название",
        base_price: "99.00",
        pack_size: "20 таблеток",
        unit_sale_preparation: {
          version: 1,
          state: "prepared",
          unit: "tablet",
          units_per_pack: 20,
          units_per_blister: null,
          dispense_step: 1,
          unit_price: "0.35",
        },
      }),
    );
    expect(calls.get).not.toHaveBeenCalled();
  });

  it("requires an explicit blister size and saves valid composition", async () => {
    setup(UNPREPARED);
    fillComposition();
    fireEvent.click(screen.getByText(HAS_BLISTER, { exact: true }));
    expect(screen.getByLabelText("Штук в одном блистере")).toHaveValue("");
    expect(screen.getByLabelText("Штук в одном блистере")).toHaveAttribute("inputmode", "numeric");
    fireEvent.change(screen.getByLabelText("Штук в одном блистере"), { target: { value: "10" } });
    save();
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith(
        "/catalog/item-1",
        expect.objectContaining({
          pack_size: "20 таблеток",
          unit_sale_preparation: expect.objectContaining({
            units_per_blister: 10,
            dispense_step: 1,
          }),
        }),
      ),
    );
  });

  it.each(["", "1", "3", "21", "2.5", "10001", "true"])(
    "rejects invalid blister size %s",
    async (value) => {
      const { onClose } = setup(UNPREPARED);
      fillComposition();
      fireEvent.click(screen.getByText(HAS_BLISTER, { exact: true }));
      fireEvent.change(screen.getByLabelText("Штук в одном блистере"), { target: { value } });
      save();
      expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
      expect(calls.patch).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["Количество в упаковке", "1"],
    ["Количество в упаковке", "10001"],
    ["Количество в упаковке", "20.5"],
    ["Цена одной штуки, TJS", "0"],
    ["Цена одной штуки, TJS", "-1"],
    ["Цена одной штуки, TJS", "0.001"],
    ["Цена одной штуки, TJS", "1000000000000.00"],
    ["Цена одной штуки, TJS", "NaN"],
  ])("rejects invalid %s=%s without saving other card edits", async (label, value) => {
    setup(UNPREPARED);
    fillComposition();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Другое название" },
    });
    save();
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
    expect(calls.patch).not.toHaveBeenCalled();
  });

  it.each([
    ["2", "0.01", "2 таблетки"],
    ["10000", "999999999999.99", "10000 таблеток"],
  ])("accepts boundary pack/blister size %s and price %s", async (pack, price, description) => {
    setup(UNPREPARED);
    fillComposition(price);
    fireEvent.change(screen.getByLabelText("Количество в упаковке"), { target: { value: pack } });
    fireEvent.click(screen.getByText(HAS_BLISTER, { exact: true }));
    fireEvent.change(screen.getByLabelText("Штук в одном блистере"), { target: { value: pack } });
    save();
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith(
        "/catalog/item-1",
        expect.objectContaining({
          pack_size: description,
          unit_sale_preparation: expect.objectContaining({
            units_per_pack: Number(pack),
            units_per_blister: Number(pack),
            unit_price: price,
          }),
        }),
      ),
    );
  });

  it("retains blister size after changing the pack and rejects incompatible composition", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Количество в упаковке"), { target: { value: "21" } });
    expect(screen.getByLabelText("Штук в одном блистере")).toHaveValue("10");
    save();
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
    expect(calls.patch).not.toHaveBeenCalled();
  });

  it("clears saved blister composition while preserving unit, pack and prices", async () => {
    setup();
    fireEvent.click(screen.getByText(HAS_BLISTER, { exact: true }));
    expect(screen.queryByLabelText("Штук в одном блистере")).not.toBeInTheDocument();
    save();
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith(
        "/catalog/item-1",
        expect.objectContaining({
          base_price: "99.00",
          pack_size: "30 капсул",
          unit_sale_preparation: { ...ITEM.unit_sale_preparation, units_per_blister: null },
        }),
      ),
    );
  });

  it("removes preparation with the common Save while retaining the prior pack description", async () => {
    setup();
    fireEvent.click(screen.getByText(ENABLE_UNITS, { exact: true }));
    expect(screen.getByLabelText("Размер упаковки")).toHaveValue(ITEM.pack_size);
    expect(screen.queryByLabelText("Количество в упаковке")).not.toBeInTheDocument();
    save();
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledExactlyOnceWith(
        "/catalog/item-1",
        expect.objectContaining({
          unit_sale_preparation: null,
          pack_size: ITEM.pack_size,
          base_price: "99.00",
        }),
      ),
    );
  });

  it("preserves a legacy step on ordinary save and resets it only with an explicit composition edit", async () => {
    setup({ ...ITEM, unit_sale_preparation: { ...ITEM.unit_sale_preparation!, dispense_step: 5 } });
    expect(screen.getByText(/Прежняя настройка: продажа по 5 шт/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Изменённое название" },
    });
    save();
    await waitFor(() => expect(calls.patch).toHaveBeenCalledTimes(1));
    expect(calls.patch.mock.calls[0]?.[1]).not.toHaveProperty("unit_sale_preparation");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить изменения" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Цена одной штуки, TJS"), { target: { value: "2.10" } });
    save();
    await waitFor(() => expect(calls.patch).toHaveBeenCalledTimes(2));
    expect(calls.patch.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        pack_size: "30 капсул",
        unit_sale_preparation: expect.objectContaining({ dispense_step: 1, unit_price: "2.10" }),
      }),
    );
  });

  it.each(["missing", "branch-only"])("guards composition with %s update permission", (kind) => {
    if (kind === "missing") auth.permissions = [];
    else auth.scope = ["branch-1"];
    setup(UNPREPARED);
    const enable = screen.queryByRole("checkbox", { name: ENABLE_UNITS });
    if (enable) expect(enable).toBeDisabled();
    expect(screen.queryByLabelText("Количество в упаковке")).not.toBeInTheDocument();
  });

  it.each(["missing", "branch-only"])(
    "shows saved composition disabled with %s update permission",
    (kind) => {
      if (kind === "missing") auth.permissions = [];
      else auth.scope = ["branch-1"];
      setup();
      expect(screen.getByLabelText("Количество в упаковке")).toHaveValue("30");
      expect(screen.getByLabelText("Количество в упаковке")).toBeDisabled();
      expect(screen.getByLabelText("Что в упаковке")).toBeDisabled();
      expect(screen.getByLabelText("Цена одной штуки, TJS")).toBeDisabled();
      expect(calls.patch).not.toHaveBeenCalled();
    },
  );

  it("keeps free text editable for unprepared cards", async () => {
    setup(UNPREPARED);
    fireEvent.change(screen.getByLabelText("Размер упаковки"), {
      target: { value: "Флакон 100 мл" },
    });
    save();
    await waitFor(() =>
      expect(calls.patch).toHaveBeenCalledWith(
        "/catalog/item-1",
        expect.objectContaining({ pack_size: "Флакон 100 мл" }),
      ),
    );
  });

  it("creates a new item with preparation in a single POST", async () => {
    const { onCreated, onClose } = setup(null);
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Новый препарат" },
    });
    fireEvent.change(screen.getByLabelText("Условия отпуска"), { target: { value: "otc" } });
    fireEvent.change(screen.getByLabelText("Условия хранения"), { target: { value: "normal" } });
    fillComposition("1,50");
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(calls.post).toHaveBeenCalledExactlyOnceWith(
      "/catalog",
      expect.objectContaining({
        brand_name: "Новый препарат",
        pack_size: "20 таблеток",
        unit_sale_preparation: expect.objectContaining({
          units_per_pack: 20,
          unit_price: "1.50",
          dispense_step: 1,
        }),
      }),
    );
    expect(calls.patch).not.toHaveBeenCalled();
  });

  it("disables saving while pending and keeps all entered fields after a failed PATCH", async () => {
    let failRequest: ((reason: Error) => void) | undefined;
    calls.patch.mockImplementationOnce(
      () =>
        new Promise<never>((_resolve, reject) => {
          failRequest = reject;
        }),
    );
    const { onClose } = setup(UNPREPARED);
    fillComposition("1.25");
    save();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Сохран|Сохраня/ })).toBeDisabled(),
    );
    failRequest?.(new Error("request failed"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Количество в упаковке")).toHaveValue("20");
    expect(screen.getByLabelText("Цена одной штуки, TJS")).toHaveValue("1.25");
    save();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledTimes(2);
  });
});
