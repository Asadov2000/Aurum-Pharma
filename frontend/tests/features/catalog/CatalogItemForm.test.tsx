import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
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

function setup(item = ITEM) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <CatalogItemForm item={item} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

describe("CatalogItemForm packaging", () => {
  beforeEach(() => {
    calls.get.mockReset();
    calls.patch.mockReset();
    calls.patch.mockResolvedValue({ data: ITEM });
    auth.permissions = ["catalog.update"];
    auth.scope = null;
  });

  it("shows typed preparation as a read-only pack size and prevents stale text in an ordinary PATCH", async () => {
    const { onClose } = setup();
    const size = screen.getByLabelText("Размер упаковки");
    expect(size).toHaveValue("30 капсул");
    expect(size).toHaveAttribute("readonly", "");
    expect(screen.getByRole("button", { name: "Изменить состав упаковки" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Название лекарства"), {
      target: { value: "Другое название" },
    });
    expect(screen.getByRole("button", { name: "Изменить состав упаковки" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledTimes(1);
    const payload: unknown = calls.patch.mock.calls[0]?.[1];
    expect(payload).toEqual(expect.objectContaining({ brand_name: "Другое название" }));
    expect(payload).not.toHaveProperty("pack_size");
    expect(payload).not.toHaveProperty("unit_sale_preparation");
  });

  it.each(["missing", "branch-only"])("guards composition editing with %s access", (kind) => {
    if (kind === "missing") auth.permissions = [];
    else auth.scope = ["branch-1"];
    setup();
    expect(screen.getByLabelText("Размер упаковки")).toHaveAttribute("readonly", "");
    expect(
      screen.queryByRole("button", { name: "Изменить состав упаковки" }),
    ).not.toBeInTheDocument();
  });

  it("keeps free-text packaging editable and saves it for unprepared cards", async () => {
    const { onClose } = setup({ ...ITEM, unit_sale_preparation: null });
    const size = screen.getByLabelText("Размер упаковки");
    expect(size).toHaveValue(ITEM.pack_size);
    expect(size).not.toHaveAttribute("readonly");
    fireEvent.change(size, { target: { value: "Флакон 100 мл" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(calls.patch).toHaveBeenCalledWith(
      "/catalog/item-1",
      expect.objectContaining({ pack_size: "Флакон 100 мл" }),
    );
  });

  it("updates the open parent from preparation responses and preserves packaging through ordinary save and removal", async () => {
    const unprepared = { ...ITEM, unit_sale_preparation: null };
    const prepared: CatalogItem = {
      ...ITEM,
      pack_size: "20 таблеток",
      unit_sale_preparation: {
        version: 1,
        state: "prepared",
        unit: "tablet",
        units_per_pack: 20,
        units_per_blister: null,
        dispense_step: 1,
        unit_price: "1.00",
      },
    };
    const cleared = { ...prepared, unit_sale_preparation: null };
    calls.get.mockResolvedValue({ data: unprepared });
    calls.patch
      .mockResolvedValueOnce({ data: prepared })
      .mockResolvedValueOnce({ data: prepared })
      .mockResolvedValueOnce({ data: cleared });
    setup(unprepared);
    fireEvent.click(screen.getByRole("button", { name: "Подготовить поштучный отпуск" }));
    await screen.findByLabelText("Количество в упаковке");
    const dialog = within(screen.getByRole("dialog", { name: "Подготовка поштучного отпуска" }));
    fireEvent.change(dialog.getByLabelText("Количество в упаковке"), {
      target: { value: "20" },
    });
    fireEvent.change(dialog.getByLabelText("Что в упаковке"), { target: { value: "tablet" } });
    fireEvent.change(dialog.getByLabelText(/Цена одной единицы/), { target: { value: "1" } });
    calls.get.mockResolvedValue({ data: prepared });
    fireEvent.click(dialog.getByRole("button", { name: "Сохранить подготовку" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Подготовка поштучного отпуска" }),
      ).not.toBeInTheDocument(),
    );
    expect(calls.patch).toHaveBeenNthCalledWith(1, "/catalog/item-1", {
      pack_size: "20 таблеток",
      unit_sale_preparation: prepared.unit_sale_preparation,
    });
    expect(screen.getByLabelText("Размер упаковки")).toHaveValue("20 таблеток");
    expect(screen.getByLabelText("Размер упаковки")).toHaveAttribute("readonly", "");
    expect(screen.getByRole("button", { name: "Изменить состав упаковки" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Подготовить поштучный отпуск" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(calls.patch).toHaveBeenCalledTimes(2));
    expect(calls.patch.mock.calls[1]?.[1]).not.toHaveProperty("pack_size");
    expect(calls.patch.mock.calls[1]?.[1]).not.toHaveProperty("unit_sale_preparation");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Изменить состав упаковки" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Изменить состав упаковки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Убрать подготовку" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Подготовка поштучного отпуска" }),
      ).not.toBeInTheDocument(),
    );
    expect(calls.patch).toHaveBeenNthCalledWith(3, "/catalog/item-1", {
      unit_sale_preparation: null,
    });
    expect(screen.getByLabelText("Размер упаковки")).toHaveValue("20 таблеток");
    expect(screen.getByLabelText("Размер упаковки")).not.toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Подготовить поштучный отпуск" })).toBeEnabled();
  });
});
