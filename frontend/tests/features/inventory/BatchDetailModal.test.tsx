import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getBatch: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      is_developer: false,
      permissions: ["batches.view", "batches.write_off"],
    },
  }),
}));

vi.mock("@/features/inventory/api", () => ({
  getBatch: (...args: unknown[]) => mocks.getBatch(...args),
  listBatches: vi.fn(),
  listMovements: vi.fn(),
  writeOff: vi.fn(),
}));

import { BatchDetailModal } from "@/features/inventory/BatchDetailModal";

const DETAILS = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "00000000-0000-0000-0000-000000000010",
  branch_id: "00000000-0000-0000-0000-000000000020",
  catalog_id: "00000000-0000-0000-0000-000000000030",
  batch_number: "LOT-2026-001",
  manufactured_at: "2026-01-15",
  expires_at: "2027-01-15",
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
  expiry_status: "normal" as const,
  days_to_expiry: 166,
  report_timezone: "Asia/Dushanbe",
  recent_movements: [
    {
      id: "00000000-0000-0000-0000-000000000040",
      batch_id: "00000000-0000-0000-0000-000000000001",
      movement_type: "incoming",
      qty_delta: "100.000",
      source_table: "incoming_item",
      source_id: "00000000-0000-0000-0000-000000000050",
      notes: null,
      created_at: "2026-05-22T00:00:00Z",
    },
  ],
};

function renderDetails() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BatchDetailModal batchId={DETAILS.id} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("BatchDetailModal", () => {
  beforeEach(() => {
    mocks.getBatch.mockReset().mockResolvedValue(DETAILS);
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("loads one complete detail resource and renders its movement history", async () => {
    renderDetails();

    expect(await screen.findByRole("heading", { name: "Парацетамол" })).toBeInTheDocument();
    expect(screen.getByText("Аптека Рудаки · партия LOT-2026-001")).toBeInTheDocument();
    expect(screen.getByText("Поступление")).toBeInTheDocument();
    expect(screen.getByText("Приход")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Списать" })).toBeInTheDocument();
    expect(mocks.getBatch).toHaveBeenCalledTimes(1);
    expect(mocks.getBatch).toHaveBeenCalledWith(DETAILS.id);
  });
});
