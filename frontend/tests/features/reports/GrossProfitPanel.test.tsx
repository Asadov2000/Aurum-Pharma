import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GrossProfitPanel } from "@/features/reports/GrossProfitPanel";
const mocks = vi.hoisted(() => ({
  query: vi.fn(),
  xlsx: vi.fn(),
  download: vi.fn(),
  active: true,
  data: null as unknown,
  refetch: vi.fn(),
}));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      id: "user",
      active_tenant_id: "tenant",
      permissions: ["reports.view", "reports.export"],
    },
  }),
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => () => mocks.active,
}));
vi.mock("@/lib/download", () => ({
  downloadBlob: (...args: unknown[]) => mocks.download(...args),
}));
vi.mock("@/features/reports/branchScope", () => ({
  useReportBranches: () => ({
    scope: null,
    canView: true,
    defaultBranchId: "",
    data: [{ id: "00000000-0000-4000-8000-000000000001", name: "Аптека 1" }],
    isError: false,
  }),
}));
vi.mock("@/features/reports/grossProfitReport", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/reports/grossProfitReport")>();
  return {
    ...original,
    useGrossProfitReport: (...args: unknown[]) => {
      mocks.query(...args);
      return {
        data: mocks.data,
        isFetching: false,
        isLoading: false,
        isError: false,
        refetch: mocks.refetch,
      };
    },
    getGrossProfitReportXlsx: (...args: unknown[]) => mocks.xlsx(...args),
  };
});
function report() {
  const base = {
    source_kind: "physical",
    event_id: "event",
    branch_id: "branch",
    branch_name: "Аптека 1",
    occurred_at: "2026-10-16T10:00:00Z",
    cost_status: "known",
  };
  return {
    date_from: "2026-10-01",
    date_to: "2026-10-16",
    currency: "TJS",
    total: 3,
    page: 1,
    page_size: 25,
    sales_revenue: "8.77",
    refunds: "0.43",
    net_revenue: "8.34",
    net_sales_cost: "4.75",
    known_net_sales_cost: "4.75",
    gross_profit: "3.59",
    known_gross_profit: "3.59",
    writeoff_cost: "2.00",
    known_writeoff_cost: "2.00",
    cost_status: "known",
    writeoff_cost_status: "known",
    rows: [
      {
        ...base,
        line_id: "1",
        name: "Проданный",
        event_kind: "sale",
        revenue: "8.77",
        cost: "5.00",
        gross_profit: "3.77",
      },
      {
        ...base,
        line_id: "2",
        name: "Возвращённый",
        event_kind: "refund",
        revenue: "-0.43",
        cost: "-0.25",
        gross_profit: "-0.18",
      },
      {
        ...base,
        line_id: null,
        name: "Списанный",
        event_kind: "writeoff",
        revenue: "0.00",
        cost: "2.00",
        gross_profit: null,
      },
    ],
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.active = true;
  mocks.data = report();
  mocks.xlsx.mockResolvedValue(new Blob(["xlsx"]));
});
afterEach(cleanup);
describe("gross profit panel", () => {
  it("separates write-offs, signed refund costs and gross from net profit", () => {
    render(<GrossProfitPanel />);
    expect(screen.getByText(/Это не чистая прибыль/)).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Итоги валовой прибыли" })).getByText(
        "Валовая прибыль: 3,59 TJS",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Стоимость списаний" })).getByText(
        "Стоимость списаний: 2,00 TJS",
      ),
    ).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("-0,25 TJS")).toBeInTheDocument();
    expect(screen.getByText("Учитывается отдельно")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Скачать в Excel" })).not.toBeInTheDocument();
  });
  it("distinguishes partial profit from a known zero and hidden costs", () => {
    mocks.data = {
      ...report(),
      cost_status: "partial",
      net_sales_cost: null,
      gross_profit: null,
      known_net_sales_cost: "0.00",
      known_gross_profit: "0.00",
      writeoff_cost_status: "hidden",
      writeoff_cost: null,
      known_writeoff_cost: null,
    };
    render(<GrossProfitPanel />);
    expect(screen.getByText("Валовая прибыль: Нет данных о стоимости")).toBeInTheDocument();
    expect(
      screen.getByText(
        /По операциям с известной стоимостью — себестоимость: 0,00 TJS, валовая прибыль: 0,00 TJS/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Стоимость списаний: Скрыто")).toBeInTheDocument();
  });
  it("validates date ordering and exports only applied filters", async () => {
    render(<GrossProfitPanel canExport />);
    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-10-17" } });
    fireEvent.change(screen.getByLabelText("Конец периода"), { target: { value: "2026-10-16" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Начало периода должно быть не позже конца",
    );
    expect(screen.getByRole("button", { name: "Скачать в Excel" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-10-15" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    await waitFor(() =>
      expect(mocks.query).toHaveBeenLastCalledWith({
        date_from: "2026-10-15",
        date_to: "2026-10-16",
        branch_id: undefined,
        page: 1,
        page_size: 25,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledTimes(1));
  });
  it("suppresses XLSX after logout", async () => {
    mocks.xlsx.mockImplementation(async () => {
      mocks.active = false;
      return new Blob(["xlsx"]);
    });
    render(<GrossProfitPanel canExport />);
    fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
    await waitFor(() => expect(mocks.xlsx).toHaveBeenCalled());
    expect(mocks.download).not.toHaveBeenCalled();
  });
  it("suppresses XLSX after scoped panel unmount", async () => {
    let finish!: (blob: Blob) => void;
    mocks.xlsx.mockReturnValue(
      new Promise<Blob>((resolve) => {
        finish = resolve;
      }),
    );
    const rendered = render(<GrossProfitPanel canExport />);
    fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
    rendered.unmount();
    finish(new Blob(["xlsx"]));
    await waitFor(() => expect(mocks.xlsx).toHaveBeenCalledTimes(1));
    expect(mocks.download).not.toHaveBeenCalled();
  });
});
