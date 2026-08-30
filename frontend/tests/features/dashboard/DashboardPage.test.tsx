import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      permissions: [
        "reports.view",
        "pos.sell",
        "incoming.view",
        "incoming.create",
        "catalog.view",
        "batches.view",
        "branches.view",
      ],
    };
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders all four sections with data", async () => {
    getDashboardSummary.mockResolvedValueOnce(SUMMARY);
    renderPage();

    // Section titles
    expect(await screen.findByText("Сегодня")).toBeInTheDocument();
    expect(screen.getByText("Сроки годности и лицензии")).toBeInTheDocument();
    expect(screen.getByText("Финансы")).toBeInTheDocument();
    expect(screen.getByText("Требует проверки")).toBeInTheDocument();

    // Today numbers
    expect(screen.getByText(/1\s500,00 TJS/)).toBeInTheDocument();
    // Expiring batch + license
    expect(screen.getByText(/LOT-7/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Лицензии/ }));
    expect(screen.getByText("Аптека №1")).toBeInTheDocument();
    // Finance overdue badge
    expect(screen.getByText(/Есть просрочка/)).toBeInTheDocument();
    // Checklist counts
    expect(screen.getByText("Черновики приёмок")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновить сводку" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Открыть кассу|Новая продажа/ }).length).toBe(2);
  });

  it("does not expose quick mutation actions without their permissions", async () => {
    mockUser = {
      home_tenant_id: "t-1",
      full_name: "Auditor",
      email: "auditor@aurum.tj",
      permissions: ["reports.view"],
    };
    getDashboardSummary.mockResolvedValueOnce(SUMMARY);
    renderPage();

    expect(await screen.findByText("Сегодня")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Открыть кассу" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Новая приёмка" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Партия LOT-7/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Открыть партии" })).not.toBeInTheDocument();
  });

  it("shows the profile fallback for a support user without a tenant", () => {
    mockUser = { is_developer: true, full_name: "Dev", email: "dev@aurum.tj" };
    renderPage();
    expect(screen.getByText(/Сводка по аптеке доступна/)).toBeInTheDocument();
    // Must NOT have called the tenant-scoped endpoint.
    expect(getDashboardSummary).not.toHaveBeenCalled();
  });

  it("bypasses the server cache for an explicit refresh", async () => {
    getDashboardSummary.mockResolvedValueOnce(SUMMARY).mockResolvedValueOnce({
      ...SUMMARY,
      today: { ...SUMMARY.today, revenue: "1750.00" },
      generated_at: "2026-05-27T10:01:00Z",
    });
    renderPage();

    await screen.findByText(/1\s500,00 TJS/);
    fireEvent.click(screen.getByRole("button", { name: "Обновить сводку" }));

    await waitFor(() => expect(getDashboardSummary).toHaveBeenLastCalledWith(true));
    expect(await screen.findByText(/1\s750,00 TJS/)).toBeInTheDocument();
  });
});
