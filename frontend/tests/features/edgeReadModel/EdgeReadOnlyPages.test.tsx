import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listEdgeCatalog = vi.fn();
const getEdgeCatalogItem = vi.fn();
const listEdgeSuppliers = vi.fn();
const listEdgeInventory = vi.fn();

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/features/settings/useQuickActionPreferences", async () => {
  const { defaultQuickActionPreferences } = await import("@/features/settings/quickActions");
  return {
    useQuickActionPreferences: (section: "catalog" | "batches") => ({
      config: defaultQuickActionPreferences(section),
      scope: "user-1:t-1",
    }),
  };
});

vi.mock("@/features/edgeReadModel/api", () => ({
  listEdgeCatalog: (...args: unknown[]) => listEdgeCatalog(...args),
  getEdgeCatalogItem: (...args: unknown[]) => getEdgeCatalogItem(...args),
  listEdgeSuppliers: (...args: unknown[]) => listEdgeSuppliers(...args),
  listEdgeInventory: (...args: unknown[]) => listEdgeInventory(...args),
}));

import {
  EdgeCatalogPage,
  EdgeInventoryPage,
  EdgeSuppliersPage,
} from "@/features/edgeReadModel/EdgeReadOnlyPages";

const plane = {
  mode: "edge-readonly" as const,
  branchId: "20000000-0000-4000-8000-000000000002",
};

const metadata = {
  snapshot_id: "20000000-0000-4000-8000-000000000003",
  source_revision: 7,
  source_generated_at: "2026-09-20T09:00:00Z",
  last_confirmed_at: "2026-09-20T10:00:00Z",
  age_seconds: 30,
  is_stale: false,
  source: "local_edge" as const,
};

describe("Edge read-only pages", () => {
  beforeEach(() => {
    listEdgeCatalog.mockReset();
    getEdgeCatalogItem.mockReset();
    listEdgeSuppliers.mockReset();
    listEdgeInventory.mockReset();
  });

  it("shows local catalog data without mutation controls", async () => {
    const item = {
      id: "item-1",
      tenant_id: "tenant-1",
      brand_name: "Парацетамол",
      inn: "paracetamol",
      manufacturer: "Aurum",
      form: "таблетки",
      dosage: "500 мг",
      pack_size: "10",
      atx_code: null,
      dispensing_type: "otc" as const,
      storage_type: "normal" as const,
      category: null,
      base_price: "5.00",
      currency: "TJS",
      image_version: null,
      is_active: true,
      deleted_at: null,
      created_at: "2026-09-20T09:00:00Z",
      updated_at: "2026-09-20T09:00:00Z",
    };
    listEdgeCatalog.mockResolvedValue({
      metadata,
      items: [item, { ...item, id: "item-2", brand_name: "Ибупрофен" }],
      total: 2,
      page: 1,
      page_size: 50,
    });
    getEdgeCatalogItem.mockResolvedValue({
      metadata,
      item: { ...item, barcodes: [{ id: "barcode-1", code: "123", code_type: "other" }] },
    });

    renderEdge(<EdgeCatalogPage plane={plane} />);

    expect(await screen.findByText("Парацетамол")).toBeInTheDocument();
    expect(screen.getByText("Локальный снимок")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /добавить|изменить|удалить/i }),
    ).not.toBeInTheDocument();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const row = screen.getByText("Ибупрофен").closest("article");
    fireEvent.contextMenu(row!);
    const menu = await screen.findByRole("menu", { name: "Действия для Ибупрофен" });
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((control) => control.textContent),
    ).toEqual(["Открыть", "Скопировать название", "Настроить меню"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Скопировать название" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith("Ибупрофен"));
    fireEvent.contextMenu(row!);
    fireEvent.click(
      within(await screen.findByRole("menu")).getByRole("menuitem", { name: "Открыть" }),
    );
    await waitFor(() =>
      expect(getEdgeCatalogItem).toHaveBeenCalledWith(
        "item-2",
        plane.branchId,
        expect.any(AbortSignal),
      ),
    );
  });

  it("shows suppliers without return or edit actions", async () => {
    listEdgeSuppliers.mockResolvedValue({
      metadata,
      items: [
        {
          id: "supplier-1",
          tenant_id: "tenant-1",
          name: "Поставщик",
          legal_name: null,
          inn_or_tin: "123",
          contact_person: "Контакт",
          phone: "+992000000000",
          email: null,
          address: null,
          notes: null,
          is_active: true,
          created_at: "2026-09-20T09:00:00Z",
          updated_at: "2026-09-20T09:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    });

    renderEdge(<EdgeSuppliersPage plane={plane} />);

    expect(await screen.findByText("Поставщик")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /возврат|изменить|добавить/i }),
    ).not.toBeInTheDocument();
  });

  it("preserves hidden purchase costs in local inventory", async () => {
    listEdgeInventory.mockResolvedValue({
      metadata,
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
      summary: {
        total_qty: "0",
        purchase_value: null,
        sale_value: "0",
        attention_count: 0,
        expired_count: 0,
        blocked_count: 0,
      },
    });

    renderEdge(<EdgeInventoryPage plane={plane} />);

    expect(await screen.findByText("Партии не найдены.")).toBeInTheDocument();
    expect(screen.queryByText(/закуп/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /списать/i })).not.toBeInTheDocument();
  });

  it("copies local batch numbers without offering stock mutations or unavailable cloud details", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    listEdgeInventory.mockResolvedValue({
      metadata,
      items: [
        {
          id: "batch-1",
          catalog_name: "Парацетамол",
          batch_number: "EDGE-007",
          branch_name: "Рудаки",
          expires_at: "2027-01-01",
          qty_remaining: "12",
          expiry_status: "normal",
          is_blocked: false,
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
      summary: {
        total_qty: "12",
        purchase_value: null,
        sale_value: "60",
        attention_count: 0,
        expired_count: 0,
        blocked_count: 0,
      },
    });
    renderEdge(<EdgeInventoryPage plane={plane} />);
    const row = (await screen.findByText("Партия EDGE-007")).closest("article");
    fireEvent.contextMenu(row!);
    const menu = await screen.findByRole("menu");
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((control) => control.textContent),
    ).toEqual(["Скопировать номер партии", "Настроить меню"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Скопировать номер партии" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith("EDGE-007"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["catalog", "inventory"])(
    "blocks previous local %s context actions while another page is loading",
    async (section) => {
      const catalogItem = {
        id: "item-1",
        brand_name: "Парацетамол",
        inn: null,
        manufacturer: null,
        base_price: null,
        currency: "TJS",
        updated_at: "2026-09-20T09:00:00Z",
      };
      const batch = {
        id: "batch-1",
        catalog_name: "Парацетамол",
        batch_number: "EDGE-007",
        branch_name: "Рудаки",
        expires_at: "2027-01-01",
        qty_remaining: "12",
        expiry_status: "normal",
        is_blocked: false,
      };
      const list = section === "catalog" ? listEdgeCatalog : listEdgeInventory;
      list.mockResolvedValueOnce({
        metadata,
        items: [section === "catalog" ? catalogItem : batch],
        total: 51,
        page: 1,
        page_size: 50,
        summary: { total_qty: "12", attention_count: 0, expired_count: 0, blocked_count: 0 },
      });
      list.mockImplementation(() => new Promise(() => undefined));
      getEdgeCatalogItem.mockResolvedValue({ metadata, item: catalogItem });
      renderEdge(
        section === "catalog" ? (
          <EdgeCatalogPage plane={plane} />
        ) : (
          <EdgeInventoryPage plane={plane} />
        ),
      );
      const actions = await screen.findByRole("button", {
        name: section === "catalog" ? "Действия для Парацетамол" : "Действия для партии EDGE-007",
      });
      const row = actions.closest("article");
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
      });
      fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
      await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(actions).toBeDisabled());
      fireEvent.contextMenu(row!);
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    },
  );
});

function renderEdge(node: React.ReactNode): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}
