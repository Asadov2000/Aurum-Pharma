import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listBatches = vi.fn();

vi.mock("@/features/inventory/api", () => ({
  listBatches: (...a: unknown[]) => listBatches(...a),
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

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BatchesPage />
    </QueryClientProvider>,
  );
}

const BATCH = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "t-1",
  branch_id: "br-1",
  catalog_id: "c-1",
  batch_number: "LOT-2026-001",
  manufactured_at: null,
  expires_at: "2026-12-31",
  purchase_price: "5.00",
  sale_price: "8.50",
  currency: "TJS",
  qty_initial: "100",
  qty_remaining: "42",
  is_blocked: false,
  block_reason: null,
  blocked_at: null,
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  expiry_status: "yellow" as const,
  days_to_expiry: 220,
};

describe("BatchesPage", () => {
  beforeEach(() => {
    listBatches.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an empty-state hint when no batches exist", async () => {
    listBatches.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 50 });
    renderPage();
    expect(
      await screen.findByText(/Партии появятся после приёмки/i),
    ).toBeInTheDocument();
  });

  it("renders batches with formatted qty and a yellow-zone badge", async () => {
    listBatches.mockResolvedValueOnce({
      items: [BATCH],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    expect(await screen.findByText("LOT-2026-001")).toBeInTheDocument();
    expect(screen.getByText(/8\.50 TJS/)).toBeInTheDocument();
    expect(screen.getByText(/через 220 дн\./)).toBeInTheDocument();
    expect(screen.getAllByText(/Жёлтая зона/).length).toBeGreaterThan(0);
  });

  it("shows a 'blocked' badge for blocked batches", async () => {
    listBatches.mockResolvedValueOnce({
      items: [{ ...BATCH, is_blocked: true, block_reason: "QC hold" }],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderPage();
    expect(await screen.findByText("LOT-2026-001")).toBeInTheDocument();
    expect(screen.getByText("блок")).toBeInTheDocument();
  });
});
