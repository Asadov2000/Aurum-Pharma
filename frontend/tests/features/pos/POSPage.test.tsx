import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getCurrentShift = vi.fn();
const openShift = vi.fn();
const listRegisters = vi.fn();

vi.mock("@/features/pos/api", () => ({
  getCurrentShift: (...a: unknown[]) => getCurrentShift(...a),
  openShift: (...a: unknown[]) => openShift(...a),
  closeShift: vi.fn(),
  getZReport: vi.fn(),
  createSale: vi.fn(),
  getSale: vi.fn(),
  addSaleItem: vi.fn(),
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
  addPayment: vi.fn(),
  completeSale: vi.fn(),
  addPrescription: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listRegisters: (...a: unknown[]) => listRegisters(...a),
  listBranches: vi.fn().mockResolvedValue([]),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

vi.mock("@/features/catalog/queries", () => ({
  useCatalogQuery: () => ({ data: undefined }),
}));

import { POSPage } from "@/features/pos/POSPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <POSPage />
    </QueryClientProvider>,
  );
}

const REGISTER = {
  id: "r-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  name: "Касса 1",
  printer_type: null,
  printer_config: null,
  is_active: true,
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const OPEN_SHIFT = {
  id: "sh-1",
  tenant_id: "t-1",
  branch_id: "b-1",
  register_id: REGISTER.id,
  opened_by_user_id: "u-1",
  closed_by_user_id: null,
  opened_at: "2026-05-23T08:00:00Z",
  closed_at: null,
  status: "open" as const,
  opening_cash: "100.00",
  closing_cash_actual: null,
  closing_cash_expected: null,
  closing_difference: null,
  totals: null,
  currency: "TJS",
  notes: null,
};

describe("POSPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getCurrentShift.mockReset();
    openShift.mockReset();
    listRegisters.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hints to create a register when none exist", async () => {
    listRegisters.mockResolvedValueOnce([]);
    renderPage();
    expect(
      await screen.findByText(/Нет активных касс\. Создайте кассу/i),
    ).toBeInTheDocument();
  });

  it("auto-selects the first register and shows the open-shift form when no shift is active", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    renderPage();
    expect(await screen.findByLabelText(/Касса на начало смены/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Открыть смену/i })).toBeInTheDocument();
    expect(window.localStorage.getItem("pos:lastRegisterId")).toBe(REGISTER.id);
  });

  it("opens the shift with the entered opening cash", async () => {
    listRegisters.mockResolvedValue([REGISTER]);
    getCurrentShift.mockResolvedValue(null);
    openShift.mockResolvedValueOnce(OPEN_SHIFT);
    renderPage();
    const cashInput = await screen.findByLabelText(/Касса на начало смены/i);
    fireEvent.change(cashInput, { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: /Открыть смену/i }));
    await waitFor(() => {
      expect(openShift).toHaveBeenCalledWith({
        register_id: REGISTER.id,
        opening_cash: "250",
      });
    });
  });
});
