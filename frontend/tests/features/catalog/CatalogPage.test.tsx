import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listCatalog = vi.fn();
const createCatalogItem = vi.fn();

vi.mock("@/features/catalog/api", () => ({
  listCatalog: (...a: unknown[]) => listCatalog(...a),
  getCatalogItem: vi.fn(),
  createCatalogItem: (...a: unknown[]) => createCatalogItem(...a),
  updateCatalogItem: vi.fn(),
  deleteCatalogItem: vi.fn(),
  findByBarcode: vi.fn(),
  addBarcode: vi.fn(),
  deleteBarcode: vi.fn(),
  uploadImport: vi.fn(),
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  getImportJob: vi.fn(),
  rollbackImport: vi.fn(),
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
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

describe("CatalogPage", () => {
  beforeEach(() => {
    listCatalog.mockReset();
    createCatalogItem.mockReset();
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
    expect(await screen.findByText("Парацетамол")).toBeInTheDocument();
    expect(screen.getByText("Paracetamol")).toBeInTheDocument();
    expect(screen.getAllByText(/Безрецептурный/).length).toBeGreaterThan(0);
    expect(screen.getByText(/5\.50 TJS/)).toBeInTheDocument();
  });

  it("rejects empty submission of the new item form", async () => {
    listCatalog.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25 });
    renderPage();
    await screen.findByText(/Каталог пуст/i);
    // Header + empty-state both expose "+ Новая позиция"; the header one is first.
    fireEvent.click(screen.getAllByRole("button", { name: /\+ Новая позиция/i })[0]!);
    const submit = await screen.findByRole("button", { name: /^Создать$/i });
    fireEvent.click(submit);
    expect(await screen.findByText(/Введите название/i)).toBeInTheDocument();
    expect(createCatalogItem).not.toHaveBeenCalled();
  });

  it("submits a new catalog item with trimmed nullable fields", async () => {
    listCatalog.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
    createCatalogItem.mockResolvedValueOnce(ITEM);
    renderPage();
    await screen.findByText(/Каталог пуст/i);
    fireEvent.click(screen.getAllByRole("button", { name: /\+ Новая позиция/i })[0]!);
    fireEvent.change(await screen.findByLabelText(/Торговое название/i), {
      target: { value: "Анальгин" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Создать$/i }));
    await waitFor(() => {
      expect(createCatalogItem).toHaveBeenCalledTimes(1);
    });
    expect(createCatalogItem).toHaveBeenCalledWith(
      expect.objectContaining({
        brand_name: "Анальгин",
        inn: null,
        manufacturer: null,
        dispensing_type: "otc",
        storage_type: "normal",
        base_price: null,
      }),
    );
  });
});
