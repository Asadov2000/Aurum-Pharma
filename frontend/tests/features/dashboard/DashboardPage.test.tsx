import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getDashboardSummary = vi.fn();

vi.mock("@/features/dashboard/api", () => ({
  getDashboardSummary: (...a: unknown[]) => getDashboardSummary(...a),
}));

// Render-time Links need a router context; stub them to plain anchors.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { DashboardPage } from "@/features/dashboard/DashboardPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DashboardPage />
    </QueryClientProvider>,
  );
}

const SUMMARY = {
  today: {
    revenue: "1500.00",
    currency: "TJS",
    receipts: 12,
    active_shifts: 2,
    cashiers_on_shift: 2,
  },
  expiring: {
    batches: [
      {
        id: "b-1",
        batch_number: "LOT-7",
        branch_id: "br-1",
        expires_at: "2026-06-15",
        days_to_expiry: 19,
        expiry_status: "red",
        qty_remaining: "8.000",
      },
    ],
    licenses: [
      {
        branch_id: "br-1",
        branch_name: "Аптека №1",
        license_expires_at: "2026-06-05",
        days_left: 9,
      },
    ],
  },
  finance: {
    subscription_status: "active",
    subscription_period_end: "2026-07-01T00:00:00Z",
    open_invoices_count: 1,
    open_invoices_total: "550.00",
    currency: "TJS",
    has_overdue: true,
  },
  checklist: {
    draft_incoming_count: 2,
    closed_shifts_count: 3,
    latest_closed_shift_id: "sh-9",
  },
  generated_at: "2026-05-27T10:00:00Z",
};

describe("DashboardPage", () => {
  beforeEach(() => {
    getDashboardSummary.mockReset();
    mockUser = {
      home_tenant_id: "t-1",
      full_name: "Owner",
      email: "owner@aurum.tj",
      permissions: ["reports.view"],
    };
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all four sections with data", async () => {
    getDashboardSummary.mockResolvedValueOnce(SUMMARY);
    renderPage();

    // Section titles
    expect(await screen.findByText("Сегодня")).toBeInTheDocument();
    expect(screen.getByText("Скоро истекает")).toBeInTheDocument();
    expect(screen.getByText("Финансы")).toBeInTheDocument();
    expect(screen.getByText("Чек-лист")).toBeInTheDocument();

    // Today numbers
    expect(screen.getByText("1500.00 TJS")).toBeInTheDocument();
    // Expiring batch + license
    expect(screen.getByText("LOT-7")).toBeInTheDocument();
    expect(screen.getByText("Аптека №1")).toBeInTheDocument();
    // Finance overdue badge
    expect(screen.getByText(/Есть просрочка/)).toBeInTheDocument();
    // Checklist counts
    expect(screen.getByText("Черновики приходов")).toBeInTheDocument();
  });

  it("shows the profile fallback for a support user without a tenant", () => {
    mockUser = { is_developer: true, full_name: "Dev", email: "dev@aurum.tj" };
    renderPage();
    expect(screen.getByText(/Сводка по аптеке доступна/)).toBeInTheDocument();
    // Must NOT have called the tenant-scoped endpoint.
    expect(getDashboardSummary).not.toHaveBeenCalled();
  });
});
