import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type CatalogItem } from "@/features/catalog/types";
import { QuickProducts } from "@/features/pos/QuickProducts";

const usePosFavoritesQuery = vi.fn();
const removeFavorite = vi.fn();
const refetchFavorites = vi.fn();

vi.mock("@/features/pos/queries", () => ({
  usePosFavoritesQuery: (...args: unknown[]) => usePosFavoritesQuery(...args),
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

  it("adds an available product and blocks an unavailable one", async () => {
    const onAdd = vi.fn().mockResolvedValue(true);
    render(<QuickProducts branchId="branch-1" onAdd={onAdd} />);

    fireEvent.click(screen.getByRole("button", { name: "Добавить Парацетамол 500 мг" }));
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("product-1", "Парацетамол 500 мг", 1));

    expect(screen.getByRole("button", { name: "Добавить Амоксициллин 250 мг" })).toBeDisabled();
  });

  it("switches between the grid and list presentations", () => {
    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);

    const grid = screen.getByRole("button", { name: "Показать товары плиткой" });
    const list = screen.getByRole("button", { name: "Показать товары списком" });
    expect(grid).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(list);
    expect(list).toHaveAttribute("aria-pressed", "true");
    expect(grid).toHaveAttribute("aria-pressed", "false");
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

    expect(screen.getByText("Избранных товаров пока нет")).toBeInTheDocument();
    expect(screen.getByText(/Найдите товар в строке поиска и нажмите звезду/i)).toBeInTheDocument();
  });

  it("lets the cashier retry after favorites fail to load", () => {
    usePosFavoritesQuery.mockReturnValue({
      data: undefined,
      error: new Error("network"),
      isLoading: false,
      refetch: refetchFavorites,
    });

    render(<QuickProducts branchId="branch-1" onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(refetchFavorites).toHaveBeenCalledTimes(1);
  });
});
