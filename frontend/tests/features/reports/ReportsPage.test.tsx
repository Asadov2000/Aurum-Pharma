import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getZReport = vi.fn();

vi.mock("@/features/reports/api", () => ({
  getZReport: (...a: unknown[]) => getZReport(...a),
}));

import { ReportsPage } from "@/features/reports/ReportsPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReportsPage />
    </QueryClientProvider>,
  );
}

const Z_REPORT = {
  shift_id: "sh-1",
  opened_at: "2026-05-23T08:00:00Z",
  closed_at: "2026-05-23T20:00:00Z",
  register_id: "r-deadbeef",
  cashier_user_id: "u-cafe1234",
  opening_cash: "100.00",
  closing_cash_actual: "1250.00",
  closing_cash_expected: "1250.00",
  closing_difference: "0.00",
  totals: {
    sales_total: 1200,
    returns_total: 50,
    by_method: { cash: 800, card: 400 },
  },
  sales_count: 12,
  returns_count: 1,
};

describe("ReportsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getZReport.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("disables 'Загрузить' until a shift_id is typed", () => {
    renderPage();
    const btn = screen.getByRole("button", { name: /Загрузить/i });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("ID смены"), {
      target: { value: "sh-1" },
    });
    expect(btn).not.toBeDisabled();
  });

  it("renders the Z-report when the API returns data", async () => {
    getZReport.mockResolvedValueOnce(Z_REPORT);
    renderPage();
    fireEvent.change(screen.getByLabelText("ID смены"), {
      target: { value: "sh-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Загрузить/i }));
    await waitFor(() => {
      expect(getZReport).toHaveBeenCalledWith("sh-1");
    });
    // "На начало" is unique to the cash card; using it avoids the duplicate
    // "Касса" string (CardTitle + Field label).
    expect(await screen.findByText("На начало")).toBeInTheDocument();
    // sales_count / returns_count
    expect(screen.getByText("12 / 1")).toBeInTheDocument();
    // matching close balance → success badge with "сходится"
    expect(screen.getByText(/сходится/i)).toBeInTheDocument();
    // Per-method row from totals.by_method
    expect(screen.getByText("Наличные")).toBeInTheDocument();
  });

  it("preloads the last-closed shift id from localStorage", () => {
    window.localStorage.setItem("pos:lastClosedShiftId", "sh-from-storage");
    renderPage();
    const input = screen.getByLabelText("ID смены") as HTMLInputElement;
    expect(input.value).toBe("sh-from-storage");
  });
});
