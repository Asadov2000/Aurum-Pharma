import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AxiosError } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createCatalogItem = vi.fn();
const getCatalogItem = vi.fn();
const listCatalog = vi.fn();
const navigation = vi.hoisted(() => ({
  blocked: false,
  proceed: vi.fn(),
  reset: vi.fn(),
  options: vi.fn(),
}));
vi.mock("@/features/catalog/api", () => ({
  createCatalogItem: (...args: unknown[]) => createCatalogItem(...args),
  getCatalogItem: (...args: unknown[]) => getCatalogItem(...args),
  listCatalog: (...args: unknown[]) => listCatalog(...args),
  updateCatalogItem: vi.fn(),
}));
vi.mock("@/features/catalog/pickerApi", () => ({
  listCatalogForPicker: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("@tanstack/react-router", () => ({
  useBlocker: (options: unknown) => {
    navigation.options(options);
    return {
      status: navigation.blocked ? "blocked" : "idle",
      proceed: navigation.proceed,
      reset: navigation.reset,
    };
  },
}));

import { CatalogCreateDialog } from "@/features/catalog/CatalogCreateDialog";

const ITEM = { id: "ec420f3b-4bf3-46c2-b63e-c69a02cf78e3", brand_name: "Новый препарат" };

function setup(selectExisting = false) {
  const onCreated = vi.fn();
  const onSelectedExisting = vi.fn();
  const onClose = vi.fn();
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const view = () => (
    <QueryClientProvider client={client}>
      <CatalogCreateDialog
        open
        onClose={onClose}
        onCreated={onCreated}
        onSelectedExisting={selectExisting ? onSelectedExisting : undefined}
      />
    </QueryClientProvider>
  );
  const rendered = render(view());
  return { onCreated, onSelectedExisting, onClose, rerender: () => rendered.rerender(view()) };
}

function completeRequired() {
  fireEvent.change(screen.getByLabelText("Торговое название"), {
    target: { value: ITEM.brand_name },
  });
  fireEvent.change(screen.getByLabelText("Условия отпуска"), { target: { value: "otc" } });
  fireEvent.change(screen.getByLabelText("Условия хранения"), { target: { value: "normal" } });
}

describe("CatalogCreateDialog", () => {
  beforeEach(() => {
    createCatalogItem.mockReset();
    createCatalogItem.mockResolvedValue(ITEM);
    getCatalogItem.mockReset();
    listCatalog.mockReset();
    listCatalog.mockResolvedValue({ items: [], total: 0 });
    getCatalogItem.mockResolvedValue({
      ...ITEM,
      dosage: "500 мг",
      pack_size: "№20",
      manufacturer: "Фарм",
    });
    navigation.blocked = false;
    navigation.options.mockClear();
    navigation.proceed.mockClear();
    navigation.reset.mockClear();
  });

  it("requires explicit dispensing, storage and barcode type, then creates one atomic payload", async () => {
    const { onCreated, onClose } = setup();
    fireEvent.change(screen.getByLabelText("Торговое название"), {
      target: { value: ITEM.brand_name },
    });
    fireEvent.change(screen.getByLabelText("Первый штрихкод (необязательно)"), {
      target: { value: "LOT-A" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    expect(await screen.findByText("Выберите условия отпуска")).toBeInTheDocument();
    expect(screen.getByText("Выберите условия хранения")).toBeInTheDocument();
    expect(screen.getByText("Выберите тип штрихкода")).toBeInTheDocument();
    expect(createCatalogItem).not.toHaveBeenCalled();
    completeRequired();
    fireEvent.change(screen.getByLabelText("Тип штрихкода"), { target: { value: "code128" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(ITEM));
    expect(createCatalogItem).toHaveBeenCalledTimes(1);
    expect(createCatalogItem.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        operation_id: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
        ),
        brand_name: ITEM.brand_name,
        dispensing_type: "otc",
        storage_type: "normal",
        initial_barcode: { code: "LOT-A", code_type: "code128" },
        dosage: null,
        pack_size: null,
      }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("freezes a lost create payload through 429 and retries exactly without duplicating the operation", async () => {
    createCatalogItem
      .mockRejectedValueOnce(new Error("reply lost"))
      .mockRejectedValueOnce(Object.assign(new AxiosError("busy"), { response: { status: 429 } }))
      .mockResolvedValueOnce(ITEM);
    const { onCreated } = setup();
    completeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Повторить создание" }));
    await waitFor(() => expect(createCatalogItem).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Торговое название")).toBeDisabled();
    expect(screen.getByLabelText("Условия хранения")).toBeDisabled();
    expect(onCreated).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить создание" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Повторить создание" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(createCatalogItem.mock.calls[1]).toEqual(createCatalogItem.mock.calls[0]);
    expect(createCatalogItem.mock.calls[2]).toEqual(createCatalogItem.mock.calls[0]);
  });

  it("allows barcode conflict correction without silently rotating the operation id", async () => {
    createCatalogItem
      .mockRejectedValueOnce(
        Object.assign(new AxiosError("conflict"), {
          response: { status: 409, data: { error: { details: { reason: "barcode_conflict" } } } },
        }),
      )
      .mockResolvedValueOnce(ITEM);
    setup();
    completeRequired();
    fireEvent.change(screen.getByLabelText("Первый штрихкод (необязательно)"), {
      target: { value: "busy-code" },
    });
    fireEvent.change(screen.getByLabelText("Тип штрихкода"), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    expect(await screen.findByText(/Этот штрихкод уже принадлежит/)).toBeInTheDocument();
    expect(screen.getByLabelText("Первый штрихкод (необязательно)")).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Первый штрихкод (необязательно)"), {
      target: { value: "free-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    await waitFor(() => expect(createCatalogItem).toHaveBeenCalledTimes(2));
    expect(createCatalogItem.mock.calls[1]?.[0].operation_id).toBe(
      createCatalogItem.mock.calls[0]?.[0].operation_id,
    );
    expect(createCatalogItem.mock.calls[1]?.[0].initial_barcode.code).toBe("free-code");
  });

  it("selects a server-confirmed duplicate instead of creating another card", async () => {
    createCatalogItem.mockRejectedValueOnce(
      Object.assign(new AxiosError("duplicate"), {
        response: {
          status: 409,
          data: { error: { details: { reason: "catalog_identity_match", catalog_id: ITEM.id } } },
        },
      }),
    );
    const { onCreated, onSelectedExisting } = setup(true);
    completeRequired();
    const submit = screen.getByRole("button", { name: "Добавить товар", exact: true });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    fireEvent.click(await screen.findByRole("button", { name: "Выбрать существующий товар" }));
    expect(onSelectedExisting).toHaveBeenCalledWith(expect.objectContaining({ id: ITEM.id }));
    expect(onCreated).not.toHaveBeenCalled();
    expect(createCatalogItem).toHaveBeenCalledTimes(1);
  });

  it("can create after a similarity search error because the server checks exact matches", async () => {
    listCatalog.mockRejectedValue(new Error("search unavailable"));
    const { onCreated } = setup(true);
    completeRequired();
    expect(await screen.findByText(/Не удалось проверить каталог/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Добавить товар", exact: true });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(ITEM));
    expect(createCatalogItem.mock.calls[0]?.[0]).not.toHaveProperty("confirm_distinct_item");
  });

  it("requires an explicit override to create a distinct item after exact 409", async () => {
    createCatalogItem
      .mockRejectedValueOnce(
        Object.assign(new AxiosError("duplicate"), {
          response: {
            status: 409,
            data: { error: { details: { reason: "catalog_identity_match", catalog_id: ITEM.id } } },
          },
        }),
      )
      .mockResolvedValueOnce(ITEM);
    const { onCreated } = setup();
    completeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    const override = await screen.findByRole("button", { name: "Создать отдельный товар" });
    await waitFor(() => expect(override).toBeEnabled());
    fireEvent.click(override);
    await waitFor(() => expect(createCatalogItem).toHaveBeenCalledTimes(2));
    expect(onCreated).toHaveBeenCalledWith(ITEM);
    expect(createCatalogItem.mock.calls[1]?.[0]).toEqual(
      expect.objectContaining({
        operation_id: createCatalogItem.mock.calls[0]?.[0].operation_id,
        confirm_distinct_item: true,
      }),
    );
  });

  it("guards dirty Escape, outside click and navigation with explicit confirmations", async () => {
    const { onClose, rerender } = setup();
    completeRequired();
    await waitFor(() =>
      expect(navigation.options.mock.lastCall?.[0].enableBeforeUnload).toBe(true),
    );
    expect(navigation.options.mock.lastCall?.[0].shouldBlockFn()).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    const discard = screen.getByRole("dialog", { name: "Закрыть карточку товара?" });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(within(discard).getByRole("button", { name: "Отмена" }));
    fireEvent.click(screen.getByRole("dialog", { name: "Добавить товар" }).parentElement!);
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Закрыть карточку товара?" })).getByRole("button", {
        name: "Отмена",
      }),
    );
    expect(screen.getByLabelText("Торговое название")).toHaveValue(ITEM.brand_name);
    navigation.blocked = true;
    rerender();
    const leave = screen.getByRole("dialog", { name: "Уйти со страницы?" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(navigation.reset).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole("dialog", { name: "Закрыть карточку товара?" }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(leave).getByRole("button", { name: "Отмена" }));
    expect(navigation.reset).toHaveBeenCalledTimes(2);
    expect(navigation.proceed).not.toHaveBeenCalled();
  });

  it("does not silently dismiss an unknown create result", async () => {
    createCatalogItem.mockRejectedValue(new Error("reply lost"));
    const { onClose } = setup();
    completeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Добавить товар", exact: true }));
    await screen.findByRole("button", { name: "Повторить создание" });
    fireEvent.keyDown(document, { key: "Escape" });
    const discard = screen.getByRole("dialog", { name: "Закрыть карточку товара?" });
    expect(
      within(discard).getByText(/создание товара может завершиться на сервере/),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(within(discard).getByRole("button", { name: "Закрыть карточку" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
