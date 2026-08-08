import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listBatches = vi.fn();

vi.mock("@/features/inventory/api", () => ({
  listBatches: (...args: unknown[]) => listBatches(...args),
  getBatch: vi.fn(),
  listMovements: vi.fn(),
  writeOff: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: vi.fn().mockResolvedValue([]),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  listRegisters: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

import { BatchesPage } from "@/features/inventory/BatchesPage";

const SUMMARY = {
  total_qty: "42.000",
  purchase_value: "210.00",
  sale_value: "357.00",
  attention_count: 1,
  expired_count: 0,
  blocked_count: 0,
};

const BATCH = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "00000000-0000-0000-0000-000000000010",
  branch_id: "00000000-0000-0000-0000-000000000020",
  catalog_id: "00000000-0000-0000-0000-000000000030",
  batch_number: "LOT-2026-001",
  manufactured_at: null,
  expires_at: "2026-12-31",
  purchase_price: "5.00",
  sale_price: "8.50",
  currency: "TJS",
  qty_initial: "100.000",
  qty_remaining: "42.000",
  is_blocked: false,
  block_reason: null,
  blocked_at: null,
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  branch_name: "Аптека Рудаки",
  catalog_name: "Парацетамол",
  catalog_form: "таблетки",
  catalog_dosage: "500 мг",
  catalog_pack_size: "20 таблеток",
  expiry_status: "yellow" as const,
  days_to_expiry: 151,
};

function setDesktop(matches: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BatchesPage />
    </QueryClientProvider>,
  );
}

describe("BatchesPage", () => {
  beforeEach(() => {
    listBatches.mockReset();
    setDesktop(true);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the operational empty state when no batches exist", async () => {
    listBatches.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, total_qty: "0", attention_count: 0 },
    });
    renderPage();

    expect(await screen.findByText("На складе пока нет партий")).toBeInTheDocument();
    expect(screen.getByText(/после принятия первого прихода/i)).toBeInTheDocument();
  });

  it("renders a named product, pharmacy context, expiry zone and summary", async () => {
    listBatches.mockResolvedValueOnce({
      items: [BATCH],
      total: 1,
      page: 1,
      page_size: 25,
      summary: SUMMARY,
    });
    renderPage();

    expect(await screen.findByText("Парацетамол")).toBeInTheDocument();
    expect(screen.getByText("Аптека Рудаки")).toBeInTheDocument();
    expect(screen.getByText("LOT-2026-001")).toBeInTheDocument();
    expect(screen.getAllByText("Жёлтая зона").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Требуют внимания")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Открыть партию LOT-2026-001 товара Парацетамол/ }),
    ).toBeInTheDocument();
  });

  it("renders one mobile card layout without the desktop table", async () => {
    setDesktop(false);
    listBatches.mockResolvedValueOnce({
      items: [{ ...BATCH, is_blocked: true, block_reason: "Карантин качества" }],
      total: 1,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, blocked_count: 1 },
    });
    renderPage();

    expect(
      await screen.findByRole("article", { name: /Парацетамол, партия LOT-2026-001/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Заблокирована: Карантин качества")).toBeInTheDocument();
  });

  it("debounces the batch-number filter before requesting data", async () => {
    listBatches.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, total_qty: "0", attention_count: 0 },
    });
    renderPage();
    await screen.findByText("На складе пока нет партий");
    listBatches.mockClear();

    fireEvent.change(screen.getByLabelText("Номер партии"), {
      target: { value: " LOT-2026 " },
    });

    await waitFor(() => {
      expect(listBatches).toHaveBeenCalledWith(
        expect.objectContaining({ batch_number: "LOT-2026", page_size: 25 }),
      );
    });
  });

  it("offers a retry after a server error", async () => {
    listBatches.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      summary: { ...SUMMARY, total_qty: "0", attention_count: 0 },
    });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить партии");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("На складе пока нет партий")).toBeInTheDocument();
    expect(listBatches).toHaveBeenCalledTimes(2);
  });
});
