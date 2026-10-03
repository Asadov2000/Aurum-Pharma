import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/catalog/fieldTemplates", () => ({
  useFieldTemplates: () => ({ data: [], isLoading: false, isError: false }),
  useCreateFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteFieldTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const listCatalog = vi.fn();
const getCatalogSummary = vi.fn();
const getCatalogItem = vi.fn();
const createCatalogItem = vi.fn();
const authState = vi.hoisted(() => ({
  permissions: ["catalog.view", "catalog.create", "catalog.update", "catalog.delete"],
}));

vi.mock("@tanstack/react-router", () => ({
  useBlocker: () => ({ status: "idle" }),
  useNavigate: () => vi.fn(),
}));

vi.mock("@/features/settings/useQuickActionPreferences", async () => {
  const { defaultQuickActionPreferences } = await import("@/features/settings/quickActions");
  return {
    useQuickActionPreferences: (section: "catalog" | "batches") => ({
      config: defaultQuickActionPreferences(section),
      scope: "user-1:t-1",
    }),
  };
});

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      active_tenant_id: "t-1",
      is_developer: false,
      support_access: null,
      permissions: authState.permissions,
    },
  }),
}));

vi.mock("@/features/catalog/api", () => ({
  listCatalog: (...a: unknown[]) => listCatalog(...a),
  getCatalogImage: vi.fn(),
  uploadCatalogImage: vi.fn(),
  deleteCatalogImage: vi.fn(),
  getCatalogItem: (...a: unknown[]) => getCatalogItem(...a),
  createCatalogItem: (...a: unknown[]) => createCatalogItem(...a),
  updateCatalogItem: vi.fn(),
  deleteCatalogItem: vi.fn(),
  restoreCatalogItem: vi.fn(),
  findByBarcode: vi.fn(),
  addBarcode: vi.fn(),
  deleteBarcode: vi.fn(),
  uploadImport: vi.fn(),
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  getImportJob: vi.fn(),
  rollbackImport: vi.fn(),
}));

vi.mock("@/features/catalog/summaryApi", () => ({
  getCatalogSummary: (...a: unknown[]) => getCatalogSummary(...a),
}));

import { CatalogPage } from "@/features/catalog/CatalogPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CatalogPage />
    </QueryClientProvider>,
  );
}

const ITEM = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "t-1",
  brand_name: "Парацетамол",
  inn: "Paracetamol",
  manufacturer: "ОАО Фарм",
  form: "таблетки",
  dosage: "500 мг",
  pack_size: "10",
  atx_code: null,
  dispensing_type: "otc" as const,
  storage_type: "normal" as const,
  category: null,
  base_price: "5.50",
  currency: "TJS",
  is_active: true,
  deleted_at: null,
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

describe("CatalogPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    authState.permissions = ["catalog.view", "catalog.create", "catalog.update", "catalog.delete"];
    listCatalog.mockReset();
    createCatalogItem.mockReset();
    getCatalogSummary.mockReset();
    getCatalogItem.mockReset();
    getCatalogItem.mockResolvedValue({ ...ITEM, barcodes: [] });
    getCatalogSummary.mockResolvedValue({
      total: 0,
      active: 0,
      inactive: 0,
      archived: 0,
      without_barcode: 0,
      without_image: 0,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the empty state when no items match", async () => {
    listCatalog.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });
    renderPage();
    expect(await screen.findByText(/Каталог пуст/i)).toBeInTheDocument();
  });

  it("renders items returned from the API", async () => {
    listCatalog.mockResolvedValueOnce({
      items: [ITEM],
      total: 1,
      page: 1,
      page_size: 25,
    });
    renderPage();
    expect((await screen.findAllByText("Парацетамол")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Paracetamol").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Без рецепта/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/5\.50 TJS/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Доступен для продажи").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Доступны для продажи").length).toBeGreaterThan(0);
  });

  it("shows accurate summary and applies the no-photo preset", async () => {
    getCatalogSummary.mockResolvedValueOnce({
      total: 12,
      active: 10,
      inactive: 2,
      archived: 3,
      without_barcode: 4,
      without_image: 5,
    });
    listCatalog.mockResolvedValue({ items: [ITEM], total: 1, page: 1, page_size: 25 });
    renderPage();

    expect((await screen.findAllByText("12")).length).toBeGreaterThan(0);
    const presets = screen.getByRole("navigation", { name: "Представления каталога" });
    fireEvent.click(within(presets).getByRole("button", { name: /Без фото 5/i }));

    await waitFor(() => {
      expect(listCatalog).toHaveBeenLastCalledWith(
        expect.objectContaining({ image_state: "without_image", lifecycle: "current" }),
        expect.any(AbortSignal),
      );
    });
  });

  it("rejects empty submission of the new item form", async () => {
    listCatalog.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });
    renderPage();
    await screen.findByText(/Каталог пуст/i);
    fireEvent.click(screen.getAllByRole("button", { name: /Добавить товар/i })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
    const submit = within(dialog).getByRole("button", { name: /^Добавить товар$/i });
    fireEvent.click(submit);
    expect(await within(dialog).findByText(/Введите название/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Выберите условия отпуска/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Выберите условия хранения/i)).toBeInTheDocument();
    expect(createCatalogItem).not.toHaveBeenCalled();
  });

  it("submits a new catalog item with trimmed nullable fields", async () => {
    listCatalog.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
    createCatalogItem.mockResolvedValueOnce(ITEM);
    renderPage();
    await screen.findByText(/Каталог пуст/i);
    fireEvent.click(screen.getAllByRole("button", { name: /Добавить товар/i })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
    fireEvent.change(within(dialog).getByLabelText(/Название лекарства/i), {
      target: { value: "Анальгин" },
    });
    const dosageForm = within(dialog).getByLabelText(/^Форма выпуска$/i) as HTMLSelectElement;
    expect(
      Array.from(dosageForm.options)
        .slice(1, 4)
        .map((option) => option.text),
    ).toEqual(["Таблетки", "Капсулы", "Сироп"]);
    fireEvent.change(dosageForm, { target: { value: "таблетки" } });
    fireEvent.change(within(dialog).getByLabelText(/^Условия отпуска$/i), {
      target: { value: "otc" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Условия хранения$/i), {
      target: { value: "normal" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^Добавить товар$/i }));
    await waitFor(() => {
      expect(createCatalogItem).toHaveBeenCalledTimes(1);
    });
    expect(createCatalogItem).toHaveBeenCalledWith(
      expect.objectContaining({
        brand_name: "Анальгин",
        inn: null,
        manufacturer: null,
        form: "таблетки",
        dispensing_type: "otc",
        storage_type: "normal",
        base_price: null,
      }),
    );
  });

  it("keeps uncommon dosage forms available for manual entry", async () => {
    listCatalog.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
    createCatalogItem.mockResolvedValueOnce(ITEM);
    renderPage();
    await screen.findByText(/Каталог пуст/i);
    fireEvent.click(screen.getAllByRole("button", { name: /Добавить товар/i })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Добавить товар" });
    fireEvent.change(within(dialog).getByLabelText(/^Форма выпуска$/i), {
      target: { value: "other" },
    });
    fireEvent.change(within(dialog).getByLabelText("Другая форма выпуска"), {
      target: { value: "Раствор для диализа" },
    });
    fireEvent.change(within(dialog).getByLabelText(/Название лекарства/i), {
      target: { value: "Тестовый товар" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Условия отпуска$/i), {
      target: { value: "otc" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Условия хранения$/i), {
      target: { value: "normal" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^Добавить товар$/i }));
    await waitFor(() => {
      expect(createCatalogItem).toHaveBeenCalledWith(
        expect.objectContaining({ form: "Раствор для диализа" }),
      );
    });
  });

  it("hides mutation controls from a view-only account", async () => {
    authState.permissions = ["catalog.view"];
    listCatalog.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });

    renderPage();

    expect(await screen.findByText(/Каталог пуст/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Добавить товар/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Загрузить из Excel/i })).not.toBeInTheDocument();
  });

  it("opens the product card with one click on an ordinary screen", async () => {
    listCatalog.mockResolvedValueOnce({ items: [ITEM], total: 1, page: 1, page_size: 25 });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть карточку Парацетамол" }));

    expect(await screen.findByRole("dialog", { name: "Карточка товара" })).toBeInTheDocument();
  });

  it("uses the right-clicked product for copying and opening, without selecting the first row", async () => {
    const second = { ...ITEM, id: "item-2", brand_name: "Ибупрофен" };
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    listCatalog.mockResolvedValue({ items: [ITEM, second], total: 2, page: 1, page_size: 25 });
    getCatalogItem.mockImplementation(async (id: string) => ({
      ...(id === second.id ? second : ITEM),
      barcodes: [],
    }));
    renderPage();
    const row = (await screen.findByRole("button", { name: "Открыть карточку Ибупрофен" })).closest(
      "article",
    );
    expect(row).not.toBeNull();
    fireEvent.contextMenu(row!, { clientX: 30, clientY: 40 });
    const menu = await screen.findByRole("menu", { name: "Действия для Ибупрофен" });
    expect(within(menu).getByText("Ибупрофен")).toBeInTheDocument();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Скопировать название" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(second.brand_name));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.contextMenu(row!);
    fireEvent.click(
      within(await screen.findByRole("menu")).getByRole("menuitem", { name: "Открыть" }),
    );
    expect(await screen.findByRole("dialog", { name: "Карточка товара" })).toBeInTheDocument();
    await waitFor(() =>
      expect(getCatalogItem).toHaveBeenLastCalledWith(second.id, expect.any(AbortSignal)),
    );
  });

  it("keeps only permitted actions in a read-only user's context menu", async () => {
    authState.permissions = ["catalog.view"];
    listCatalog.mockResolvedValue({ items: [ITEM], total: 1, page: 1, page_size: 25 });
    renderPage();
    const row = (
      await screen.findByRole("button", { name: "Открыть карточку Парацетамол" })
    ).closest("article");
    fireEvent.contextMenu(row!);
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Открыть" })).toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: "Скопировать штрихкод" }),
    ).toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: /Изменить|Архивировать|Восстановить/ }),
    ).not.toBeInTheDocument();
  });

  it("opens the barcode picker for the right-clicked product", async () => {
    const second = { ...ITEM, id: "item-2", brand_name: "Ибупрофен" };
    listCatalog.mockResolvedValue({ items: [ITEM, second], total: 2, page: 1, page_size: 25 });
    getCatalogItem.mockResolvedValue({
      ...second,
      barcodes: [{ id: "barcode-2", code: "SECOND-PACK", code_type: "other" }],
    });
    renderPage();
    const row = (await screen.findByRole("button", { name: "Открыть карточку Ибупрофен" })).closest(
      "article",
    );
    fireEvent.contextMenu(row!);
    fireEvent.click(
      within(await screen.findByRole("menu")).getByRole("menuitem", {
        name: "Скопировать штрихкод",
      }),
    );
    const picker = await screen.findByRole("dialog", { name: "Скопировать штрихкод" });
    expect(await within(picker).findByText("SECOND-PACK")).toBeInTheDocument();
    expect(getCatalogItem).toHaveBeenCalledWith(second.id, expect.any(AbortSignal));
  });

  it("offers restore only for an archived product, according to its delete permission", async () => {
    listCatalog.mockResolvedValue({
      items: [{ ...ITEM, deleted_at: "2026-10-01T00:00:00Z" }],
      total: 1,
      page: 1,
      page_size: 25,
    });
    renderPage();
    fireEvent.contextMenu(
      (await screen.findByRole("button", { name: "Открыть карточку Парацетамол" })).closest(
        "article",
      )!,
    );
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Восстановить" })).toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: /Архивировать|Изменить/ }),
    ).not.toBeInTheDocument();
  });

  it("blocks context actions on previous catalog results while changing pages", async () => {
    listCatalog.mockResolvedValueOnce({ items: [ITEM], total: 26, page: 1, page_size: 25 });
    listCatalog.mockImplementation(() => new Promise(() => undefined));
    renderPage();
    const row = (
      await screen.findByRole("button", { name: "Открыть карточку Парацетамол" })
    ).closest("article");
    // The initial filter debounce resets page to 1; settle it before navigating.
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    });
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    await waitFor(() => expect(listCatalog).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(within(row!).getByRole("button", { name: "Действия для Парацетамол" })).toBeDisabled(),
    );
    fireEvent.contextMenu(row!);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps an imported uncommon dosage form when editing a product", async () => {
    const uncommonItem = { ...ITEM, form: "Раствор для диализа" };
    listCatalog.mockResolvedValueOnce({ items: [uncommonItem], total: 1, page: 1, page_size: 25 });
    getCatalogItem.mockResolvedValue({ ...uncommonItem, barcodes: [] });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть карточку Парацетамол" }));
    const card = await screen.findByRole("dialog", { name: "Карточка товара" });
    fireEvent.click(within(card).getByRole("button", { name: "Изменить позицию" }));

    const editor = await screen.findByRole("dialog", { name: "Изменить товар: Парацетамол" });
    expect(within(editor).getByLabelText(/^Форма выпуска$/i)).toHaveValue("other");
    expect(within(editor).getByLabelText("Другая форма выпуска")).toHaveValue(
      "Раствор для диализа",
    );
  });
});
