import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createCatalogItem = vi.fn();
const saveDraft = vi.fn();
const commitDraft = vi.fn();
const routerBlocker = vi.fn();
let permissions = ["catalog.create"];

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { is_developer: false, permissions } }),
}));
vi.mock("@/features/catalog/api", () => ({
  createCatalogItem: (...args: unknown[]) => createCatalogItem(...args),
  listCatalog: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  updateCatalogItem: vi.fn(),
}));
vi.mock("@/features/catalog/pickerApi", () => ({
  listCatalogForPicker: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("@/features/incoming/api", () => ({
  saveIncomingDraftRow: (...args: unknown[]) => saveDraft(...args),
  commitIncomingDraftRow: (...args: unknown[]) => commitDraft(...args),
  listIncomingDraftRows: vi.fn(),
  updateIncomingItem: vi.fn(),
}));
vi.mock("@tanstack/react-router", () => ({
  useBlocker: (...args: unknown[]) => {
    routerBlocker(...args);
    return { status: "idle" };
  },
}));
vi.mock("@/features/catalog/CatalogPicker", () => ({
  CatalogPicker: ({
    id,
    initialLabel,
    onSearchChange,
  }: {
    id: string;
    initialLabel?: string;
    onSearchChange?: (text: string) => void;
  }) => (
    <input
      id={id}
      value={initialLabel ?? ""}
      onChange={(event) => onSearchChange?.(event.target.value)}
    />
  ),
}));

import { AddItemForm } from "@/features/incoming/AddItemForm";

const ITEM = { id: "ec420f3b-4bf3-46c2-b63e-c69a02cf78e3", brand_name: "Новый препарат 500 мг" };

function setup() {
  const onClose = vi.fn();
  const onDirty = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AddItemForm documentId="doc-1" onClose={onClose} onDirtyChange={onDirty} />
    </QueryClientProvider>,
  );
  return { onClose, onDirty };
}

function enterIncoming() {
  fireEvent.change(screen.getByLabelText("Количество единиц"), { target: { value: "12," } });
  fireEvent.change(screen.getByLabelText("Закупочная цена за единицу"), {
    target: { value: "4,50" },
  });
  fireEvent.change(screen.getByLabelText("Розничная цена за единицу"), {
    target: { value: "6,20" },
  });
  fireEvent.change(screen.getByLabelText("Срок годности"), { target: { value: "2099-12-31" } });
  fireEvent.change(screen.getByLabelText("Лекарство или товар"), {
    target: { value: ITEM.brand_name },
  });
}

async function completeCatalog() {
  const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
  expect(within(dialog).getByLabelText("Торговое название")).toHaveValue(ITEM.brand_name);
  expect(within(dialog).getByLabelText("Дозировка")).toHaveValue("");
  expect(within(dialog).getByLabelText("Размер упаковки")).toHaveValue("");
  fireEvent.change(within(dialog).getByLabelText("Условия отпуска"), { target: { value: "otc" } });
  fireEvent.change(within(dialog).getByLabelText("Условия хранения"), {
    target: { value: "normal" },
  });
  fireEvent.change(within(dialog).getByLabelText("Первый штрихкод (необязательно)"), {
    target: { value: "code-new" },
  });
  fireEvent.change(within(dialog).getByLabelText("Тип штрихкода"), {
    target: { value: "code128" },
  });
  const submit = within(dialog).getByRole("button", { name: "Добавить товар", exact: true });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
}

describe("catalog creation from an incoming draft", () => {
  beforeEach(() => {
    permissions = ["catalog.create"];
    createCatalogItem.mockReset();
    createCatalogItem.mockResolvedValue(ITEM);
    commitDraft.mockReset();
    routerBlocker.mockReset();
    saveDraft.mockReset();
    saveDraft.mockImplementation((_document, id, payload) => ({
      id,
      revision: payload.expected_revision + 1,
      fields: payload.fields,
    }));
  });

  it("requires catalog.create permission", () => {
    permissions = [];
    setup();
    expect(screen.queryByRole("button", { name: "Новый товар" })).not.toBeInTheDocument();
  });

  it("saves raw input first, creates in a portal, selects the result and never submits the incoming form", async () => {
    const { onClose } = setup();
    enterIncoming();
    const incomingForm = screen.getByLabelText("Количество единиц").closest("form");
    fireEvent.click(screen.getByRole("button", { name: /Проверить и создать «/ }));
    const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
    expect(saveDraft).toHaveBeenCalledTimes(1);
    expect(saveDraft.mock.calls[0]?.[2].fields).toEqual(
      expect.objectContaining({
        qty: "12,",
        purchase_price: "4,50",
        sale_price: "6,20",
        expires_at: "2099-12-31",
      }),
    );
    expect(incomingForm?.contains(dialog)).toBe(false);
    await completeCatalog();
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Добавить товар" })).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(2));
    expect(saveDraft.mock.calls[1]?.[2].fields).toEqual(
      expect.objectContaining({
        catalog_id: ITEM.id,
        qty: "12,",
        purchase_price: "4,50",
        sale_price: "6,20",
        expires_at: "2099-12-31",
      }),
    );
    expect(screen.getByLabelText("Лекарство или товар")).toHaveValue(ITEM.brand_name);
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    expect(commitDraft).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(routerBlocker).not.toHaveBeenCalled();
  });

  it("does not open creation when the incoming snapshot could not be saved", async () => {
    saveDraft.mockRejectedValue(new Error("offline"));
    setup();
    enterIncoming();
    fireEvent.click(screen.getByRole("button", { name: /Проверить и создать «/ }));
    expect(await screen.findByText(/Сначала сохраните строку прихода/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Добавить товар" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    expect(createCatalogItem).not.toHaveBeenCalled();
  });

  it("keeps the created item selected when saving its incoming link fails", async () => {
    saveDraft
      .mockResolvedValueOnce({ revision: 1 })
      .mockRejectedValueOnce(new Error("link reply lost"));
    setup();
    enterIncoming();
    fireEvent.click(screen.getByRole("button", { name: /Проверить и создать «/ }));
    await completeCatalog();
    expect(await screen.findByText(/Товар выбран/)).toBeInTheDocument();
    expect(screen.getByLabelText("Лекарство или товар")).toHaveValue(ITEM.brand_name);
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    expect(createCatalogItem).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Повторить сохранение" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить создание" })).not.toBeInTheDocument();
    expect(commitDraft).not.toHaveBeenCalled();
  });

  it("reports child dirtiness to the existing incoming guard and preserves raw input on cancel", async () => {
    const { onDirty } = setup();
    enterIncoming();
    fireEvent.click(screen.getByRole("button", { name: /Проверить и создать «/ }));
    const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
    fireEvent.change(within(dialog).getByLabelText("Производитель"), { target: { value: "Фарм" } });
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    const discard = screen.getByRole("dialog", { name: "Закрыть карточку товара?" });
    fireEvent.click(within(discard).getByRole("button", { name: "Закрыть карточку" }));
    expect(screen.getByLabelText("Количество единиц")).toHaveValue("12,");
    expect(routerBlocker).not.toHaveBeenCalled();
  });
});
