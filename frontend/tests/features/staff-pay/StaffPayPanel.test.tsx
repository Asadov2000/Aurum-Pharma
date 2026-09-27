import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type StaffPayList, type StaffPayPeriod } from "@/features/staff-pay/types";

const period: StaffPayPeriod = {
  id: "period",
  employee_id: "own",
  period_from: "2026-09-01",
  period_to: "2026-09-27",
  amount: "1000.00",
  paid_amount: "500.00",
  advance_amount: "200.00",
  remaining_amount: "500.00",
  currency: "TJS",
  note: "Согласовано вручную",
  row_version: 1,
  created_at: "2026-09-27T00:00:00Z",
  updated_at: "2026-09-27T00:00:00Z",
};
const state = vi.hoisted(() => ({
  data: undefined as StaffPayList<StaffPayPeriod> | undefined,
  isPending: false,
  isFetching: false,
  isError: false,
  error: new Error("unavailable"),
  refetch: vi.fn(),
  query: vi.fn(),
  manage: true,
}));
vi.mock("@/features/staff-pay/queries", () => ({
  useStaffPayAccess: () => ({ scope: ["own", "tenant", null], manage: state.manage }),
  useStaffPayPeriods: (...args: unknown[]) => {
    state.query(...args);
    return state;
  },
}));
vi.mock("@/features/staff-pay/PayForm", () => ({ PayForm: () => null }));
vi.mock("@/features/staff-pay/PayHistory", () => ({
  PayHistory: () => null,
  ReadError: () => <p role="alert">Не удалось загрузить сведения</p>,
}));
import {
  PersonalStaffPayPanel,
  PeriodCard,
  StaffPayPanel,
} from "@/features/staff-pay/StaffPayPanel";
import { payMoney } from "@/features/staff-pay/format";

describe("private manual salary", () => {
  beforeEach(() => {
    state.data = { items: [period], total: 1, page: 1, page_size: 10, timezone: "Asia/Dushanbe" };
    state.isError = false;
    state.manage = true;
    state.query.mockClear();
  });
  it("requires explicit reveal, never grants own manager actions, and hides details again", () => {
    render(<PersonalStaffPayPanel />);
    expect(state.query).toHaveBeenLastCalledWith(true, null, 1, false);
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать сведения" }));
    expect(state.query).toHaveBeenLastCalledWith(true, null, 1, true);
    expect(screen.getByText(period.note)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записать выдачу" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Скрыть сведения" }));
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
  });
  it("shows server totals without adding advance twice and describes overpayment separately", () => {
    render(
      <PeriodCard
        period={{ ...period, remaining_amount: "-25.00" }}
        timezone="Asia/Dushanbe"
        manage={false}
        onIntent={vi.fn()}
        onHistory={vi.fn()}
      />,
    );
    expect(screen.getByText("Из них авансы").nextElementSibling).toHaveTextContent(
      payMoney("200.00"),
    );
    expect(screen.getByText("Выдано наличными").nextElementSibling).toHaveTextContent(
      payMoney("500.00"),
    );
    expect(screen.getByText("Выдано сверх суммы").nextElementSibling).toHaveTextContent(
      payMoney("25.00"),
    );
    expect(screen.queryByText("Осталось выдать")).not.toBeInTheDocument();
  });
  it("hides stale salary details on failed revalidation and disallows write action", () => {
    state.isError = true;
    render(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить период" })).toBeDisabled();
  });
  it("keeps management read-only for delegated view permission", () => {
    state.manage = false;
    render(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.getByRole("button", { name: "История выдач" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить сумму" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить период" })).not.toBeInTheDocument();
  });
});
