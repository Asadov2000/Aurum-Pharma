import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type CatalogItem } from "@/features/catalog/types";
import { QuickProducts } from "@/features/pos/QuickProducts";

const usePosFavoritesQuery = vi.fn();
const useCatalogQuery = vi.fn();
const addFavorite = vi.fn();
const removeFavorite = vi.fn();
const refetchFavorites = vi.fn();
const refetchCatalog = vi.fn();

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: (...args: unknown[]) => useCatalogQuery(...args),
}));

vi.mock("@/features/pos/queries", () => ({
  usePosFavoritesQuery: (...args: unknown[]) => usePosFavoritesQuery(...args),
  useAddPosFavorite: () => ({
    isPending: false,
    mutateAsync: (...args: unknown[]) => addFavorite(...args),
  }),
  useRemovePosFavorite: () => ({
    isPending: false,
    mutateAsync: (...args: unknown[]) => removeFavorite(...args),
  }),
}));

const products: CatalogItem[] = [
  {
    id: "product-1",
    tenant_id: "tenant-1",
    brand_name: "Парацетамол 500 мг",
    inn: "Парацетамол",
    manufacturer: "Aurum",
    form: "таблетки",
    dosage: "500 мг",
    pack_size: "20 таблеток",
    atx_code: null,
    dispensing_type: "otc",
    storage_type: "normal",
    category: "Обезболивающие",
    base_price: "6.50",
    currency: "TJS",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    stock_available: "148",
  },
  {
    id: "product-2",
    tenant_id: "tenant-1",
    brand_name: "Амоксициллин 250 мг",
    inn: "Амоксициллин",
    manufacturer: "Aurum",
    form: "капсулы",
    dosage: "250 мг",
    pack_size: "20 капсул",
    atx_code: null,
    dispensing_type: "prescription",
    storage_type: "normal",
    category: "Антибиотики",
    base_price: "12.80",
    currency: "TJS",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    stock_available: "0",
  },
];

describe("QuickProducts", () => {
  beforeEach(() => {
    useCatalogQuery.mockReset();
    useCatalogQuery.mockReturnValue({
      data: { items: products, total: products.length, page: 1, page_size: 20 },
      error: null,
      isLoading: false,
      isFetching: false,
      isPlaceholderData: false,
      refetch: refetchCatalog,
    });
    addFavorite.mockReset();
    addFavorite.mockResolvedValue(undefined);
    removeFavorite.mockReset();
    removeFavorite.mockResolvedValue(undefined);
    refetchFavorites.mockReset();
    refetchFavorites.mockResolvedValue(undefined);
    usePosFavoritesQuery.mockReturnValue({
      data: products.map((catalog, index) => ({
        id: `favorite-${index + 1}`,
        catalog_id: catalog.id,
        created_at: "2026-01-01T00:00:00Z",
        catalog,
      })),
      error: null,
      isLoading: false,
      refetch: refetchFavorites,
    });
  });

  it("selects an available product and blocks an unavailable one", async () => {
    const onAdd = vi.fn().mockResolvedValue(true);
    render(<QuickProducts branchId="branch-1" onAdd={onAdd} />);

    fireEvent.click(screen.getByRole("button", { name: "Выбрать Парацетамол 500 мг" }));
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("product-1", "Парацетамол 500 мг", 1));

    expect(screen.getByRole("button", { name: "Выбрать Амоксициллин 250 мг" })).toBeDisabled();
  });

  it("switches between the grid and list presentations", () => {
    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);

    const grid = screen.getByRole("button", { name: "Показать товары плиткой" });
    const list = screen.getByRole("button", { name: "Показать товары списком" });
    expect(list).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(grid);
    expect(grid).toHaveAttribute("aria-pressed", "true");
    expect(list).toHaveAttribute("aria-pressed", "false");
  });

  it("removes only the selected personal favorite", async () => {
    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Убрать Парацетамол 500 мг из избранного" }),
    );

    await waitFor(() => expect(removeFavorite).toHaveBeenCalledWith("product-1"));
  });

  it("explains how to add the first favorite", () => {
    usePosFavoritesQuery.mockReturnValue({
      data: [],
      error: null,
      isLoading: false,
      refetch: refetchFavorites,
    });

    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Избранное", exact: true }));
    expect(screen.getByText("Избранных товаров пока нет")).toBeInTheDocument();
    expect(
      screen.getByText(/Найдите товар в строке поиска или во вкладке «Все»/i),
    ).toBeInTheDocument();
  });

  it("lets the cashier retry after favorites fail to load", () => {
    usePosFavoritesQuery.mockReturnValue({
      data: undefined,
      error: new Error("network"),
      isLoading: false,
      refetch: refetchFavorites,
    });

    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Избранное", exact: true }));
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(refetchFavorites).toHaveBeenCalledTimes(1);
  });

  it("scopes all products to the branch and never fetches an unscoped catalog", () => {
    const { rerender } = render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);
    expect(useCatalogQuery).toHaveBeenLastCalledWith(
      { branch_id: "branch-1", lifecycle: "active", page: 1, page_size: 20 },
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Избранное", exact: true }));
    expect(useCatalogQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ branch_id: "branch-1" }),
      false,
    );
    rerender(<QuickProducts onAdd={vi.fn()} />);
    expect(useCatalogQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ branch_id: undefined }),
      false,
    );
    expect(screen.getByText("Выберите аптеку для просмотра товаров.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Выбрать Парацетамол 500 мг" }),
    ).not.toBeInTheDocument();
  });

  it("paginates a bounded catalog and resets the page when the branch changes", () => {
    useCatalogQuery.mockReturnValue({ data: { items: products, total: 41 }, isFetching: false });
    const { rerender } = render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(useCatalogQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2, page_size: 20 }),
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    rerender(<QuickProducts branchId="branch-2" onAdd={vi.fn()} />);
    expect(useCatalogQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ branch_id: "branch-2", page: 1 }),
      true,
    );
  });

  it("hides placeholder products while the next branch or page is loading", () => {
    useCatalogQuery.mockReturnValue({
      data: { items: products, total: 41 },
      isPlaceholderData: true,
      isFetching: true,
    });
    render(<QuickProducts branchId="branch-2" onAdd={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: "Выбрать Парацетамол 500 мг" }),
    ).not.toBeInTheDocument();
  });

  it("highlights the selected product, keeps accounting units and blocks selection while busy", () => {
    render(
      <QuickProducts branchId="branch-1" selectedCatalogId="product-1" onAdd={vi.fn()} busy />,
    );
    const selected = screen.getByRole("button", { name: "Выбрать Парацетамол 500 мг" });
    expect(selected).toHaveAttribute("aria-pressed", "true");
    expect(selected).toBeDisabled();
    expect(screen.getByText("Остаток: 148 учётных ед.")).toBeInTheDocument();
    expect(screen.getAllByText("TJS · базовая цена")).toHaveLength(2);
  });

  it("adds a catalog product to favorites without selecting it", async () => {
    usePosFavoritesQuery.mockReturnValue({ data: [], isLoading: false, error: null });
    const onAdd = vi.fn();
    render(<QuickProducts branchId="branch-1" onAdd={onAdd} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Добавить Парацетамол 500 мг в избранное" }),
    );
    await waitFor(() => expect(addFavorite).toHaveBeenCalledWith("product-1"));
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("passes safe catalog metadata to the selection handler without adding to the receipt", async () => {
    const onAdd = vi.fn();
    const onSelect = vi.fn().mockResolvedValue(undefined);
    render(<QuickProducts branchId="branch-1" onAdd={onAdd} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Выбрать Парацетамол 500 мг" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(products[0]));
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("checks physical options even when accounting stock is zero but keeps archived products blocked", async () => {
    const physical = {
      ...products[1],
      unit_sale_preparation: {
        version: 1,
        state: "prepared",
        unit: "capsule",
        units_per_pack: 20,
        dispense_step: 1,
        unit_price: "0.64",
      },
    };
    const archived = { ...products[0], deleted_at: "2026-11-06T00:00:00Z" };
    useCatalogQuery.mockReturnValue({ data: { items: [physical, archived], total: 2 } });
    const onSelect = vi.fn().mockResolvedValue(true);
    const onAdd = vi.fn();
    render(<QuickProducts branchId="branch-1" onAdd={onAdd} onSelect={onSelect} />);
    const product = screen.getByRole("button", { name: "Выбрать Амоксициллин 250 мг" });
    expect(product).toBeEnabled();
    expect(screen.getByText("Остаток проверяется при выборе")).toBeInTheDocument();
    fireEvent.click(product);
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(physical));
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Выбрать Парацетамол 500 мг" })).toBeDisabled();
  });
});
