import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuarantineOnDatePanel } from "@/features/reports/QuarantineOnDatePanel";
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
vi.mock("@/features/reports/quarantineReport", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/reports/quarantineReport")>();
  return {
    ...original,
    useQuarantineReport: (...args: unknown[]) => {
      mocks.query(...args);
      return {
        data: mocks.data,
        isFetching: false,
        isLoading: false,
        isError: false,
        refetch: mocks.refetch,
      };
    },
    getQuarantineReportXlsx: (...args: unknown[]) => mocks.xlsx(...args),
  };
});
function report() {
  const base = {
    source_kind: "physical",
    intake_operation_id: "intake",
    line_id: "line",
    branch_id: "branch",
    branch_name: "Аптека 1",
    received_at: "2026-10-16T10:00:00Z",
  };
  return {
    on_date: "2026-10-16",
    currency: "TJS",
    total: 26,
    page: 1,
    page_size: 25,
    total_qty: "1.5",
    physical_quantities: [{ unit: "tablet", quantity: "2" }],
    total_value: null,
    known_total_value: "12.00",
    cost_status: "partial",
    rows: [
      {
        ...base,
        stock_id: "1",
        name: "Обычный",
        inventory_kind: "legacy",
        qty: "1.5",
        physical_quantity: null,
        cost_status: "known",
        value: "12.00",
      },
      {
        ...base,
        stock_id: "2",
        name: "Поштучный",
        inventory_kind: "physical",
        qty: "0",
        physical_quantity: { unit: "tablet", quantity: "2" },
        cost_status: "unknown",
        value: null,
      },
      {
        ...base,
        stock_id: "3",
        name: "Скрытый",
        inventory_kind: "physical",
        qty: "0",
        physical_quantity: { unit: "capsule", quantity: "3" },
        cost_status: "hidden",
        value: null,
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
describe("quarantine on date panel", () => {
  it("separates packs and physical units, known subtotal, unknown and hidden costs", () => {
    render(<QuarantineOnDatePanel />);
    expect(screen.getByRole("region", { name: "Карантин на дату" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("1,5 уп.")).toBeInTheDocument();
    expect(within(table).getByText("2 табл.")).toBeInTheDocument();
    expect(within(table).getByText("Нет данных о стоимости")).toBeInTheDocument();
    expect(within(table).getByText("Скрыто")).toBeInTheDocument();
    expect(screen.getByText(/Известная закупочная стоимость/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Скачать в Excel" })).not.toBeInTheDocument();
  });
  it("keeps export bound to applied date and branch and resets pagination on applying", async () => {
    render(<QuarantineOnDatePanel canExport />);
    fireEvent.change(screen.getByLabelText("Дата карантина"), { target: { value: "2026-10-15" } });
    fireEvent.change(screen.getByLabelText("Аптечная точка"), {
      target: { value: "00000000-0000-4000-8000-000000000001" },
    });
    expect(screen.getByRole("button", { name: "Скачать в Excel" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    await waitFor(() =>
      expect(mocks.query).toHaveBeenLastCalledWith({
        date: "2026-10-15",
        branch_id: "00000000-0000-4000-8000-000000000001",
        page: 1,
        page_size: 25,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledTimes(1));
    expect(mocks.xlsx).toHaveBeenCalledWith({
      date: "2026-10-15",
      branch_id: "00000000-0000-4000-8000-000000000001",
      page: 1,
      page_size: 25,
    });
  });
  it("does not download a late response after logout", async () => {
    mocks.xlsx.mockImplementation(async () => {
      mocks.active = false;
      return new Blob(["xlsx"]);
    });
    render(<QuarantineOnDatePanel canExport />);
    fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
    await waitFor(() => expect(mocks.xlsx).toHaveBeenCalled());
    expect(mocks.download).not.toHaveBeenCalled();
  });
});

it("does not export after the scoped panel unmounts", async () => {
  let resolve!: (blob: Blob) => void;
  mocks.xlsx.mockReturnValue(
    new Promise<Blob>((finish) => {
      resolve = finish;
    }),
  );
  const rendered = render(<QuarantineOnDatePanel canExport />);
  fireEvent.click(screen.getByRole("button", { name: "Скачать в Excel" }));
  rendered.unmount();
  resolve(new Blob(["xlsx"]));
  await waitFor(() => expect(mocks.xlsx).toHaveBeenCalledTimes(1));
  expect(mocks.download).not.toHaveBeenCalled();
});
