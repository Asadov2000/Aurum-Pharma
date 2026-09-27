import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type StaffPayList, type StaffPayPeriod } from "@/features/staff-pay/types";
import { type PayIntent } from "@/features/staff-pay/PayForm";

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
  scope: ["own", "tenant", null],
}));
vi.mock("@/features/staff-pay/queries", () => ({
  useStaffPayAccess: () => ({
    scope: state.scope,
    manage: state.manage,
    ordinary: true,
    view: true,
  }),
  useStaffPayPeriods: (...args: unknown[]) => {
    state.query(...args);
    return state;
  },
}));
vi.mock("@/features/staff-pay/PayForm", () => ({
  PayForm: ({
    intent,
    contextUnavailable,
  }: {
    intent: PayIntent;
    contextUnavailable?: boolean;
  }) => (
    <div role="dialog" aria-label="Тестовая форма">
      {contextUnavailable
        ? "Сведения недоступны"
        : intent.kind === "create"
          ? "Новый период"
          : `Версия периода: ${intent.period.row_version}`}
    </div>
  ),
}));
vi.mock("@/features/staff-pay/PayHistory", () => ({
  PayHistory: () => <div role="dialog" aria-label="Тестовая история" />,
  PayHistoryPreview: ({ periodId }: { periodId: string }) => (
    <section aria-label="Выдачи за выбранный период">История периода: {periodId}</section>
  ),
  ReadError: () => <p role="alert">Не удалось загрузить сведения</p>,
}));
import {
  PersonalStaffPayPanel,
  PeriodCard,
  StaffPayPanel,
} from "@/features/staff-pay/StaffPayPanel";
import { payDate, payMoney } from "@/features/staff-pay/format";

describe("private manual salary", () => {
  beforeEach(() => {
    state.data = { items: [period], total: 1, page: 1, page_size: 10, timezone: "Asia/Dushanbe" };
    state.isError = false;
    state.isPending = false;
    state.isFetching = false;
    state.manage = true;
    state.scope = ["own", "tenant", null];
    state.query.mockClear();
  });
  it("requires explicit reveal, never grants own manager actions, and hides details again", () => {
    render(<PersonalStaffPayPanel />);
    expect(state.query).toHaveBeenLastCalledWith(true, null, 1, false);
    expect(screen.getByRole("heading", { name: "Зарплата и выплаты" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Сведения скрыты" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Показать сведения" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать сведения" }));
    expect(state.query).toHaveBeenLastCalledWith(true, null, 1, true);
    expect(screen.getByText(period.note)).toBeInTheDocument();
    expect(screen.getByText("Осталось выдать").nextElementSibling).toHaveTextContent(
      payMoney(period.remaining_amount),
    );
    expect(screen.getByRole("button", { name: "История выдач" })).toBeInTheDocument();
    expect(screen.queryByText("Последние выдачи")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записать выдачу" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Скрыть сведения" }));
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
  });
  it("hides revealed personal amounts after a failed refresh", () => {
    const view = render(<PersonalStaffPayPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Показать сведения" }));
    expect(screen.getByText(period.note)).toBeInTheDocument();
    state.isError = true;
    view.rerender(<PersonalStaffPayPanel />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "История выдач" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Скрыть сведения" }));
    expect(screen.getByRole("heading", { name: "Сведения скрыты" })).toBeInTheDocument();
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
    expect(
      screen.getByText("Выдано сверх суммы", { selector: "dt" }).nextElementSibling,
    ).toHaveTextContent(payMoney("25.00"));
    expect(screen.queryByText("Осталось выдать")).not.toBeInTheDocument();
    expect(screen.queryByText(/долг/i)).not.toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Записать выдачу" })).not.toBeInTheDocument();
  });
  it("selects a previous period and uses refreshed amounts and version for its edit", () => {
    const previous = {
      ...period,
      id: "previous",
      period_from: "2026-08-01",
      period_to: "2026-08-31",
      note: "Предыдущий период",
    };
    state.data = { ...state.data!, items: [period, previous], total: 2 };
    const view = render(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.getByText("История периода: period")).toBeInTheDocument();
    expect(screen.queryByText(previous.note)).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: `Открыть период ${payDate(previous.period_from)} — ${payDate(previous.period_to)}`,
      }),
    );
    const summary = screen.getByRole("region", { name: "Выбранный период зарплаты" });
    expect(within(summary).getByText(previous.note)).toBeInTheDocument();
    expect(screen.getByText("История периода: previous")).toBeInTheDocument();
    state.data = {
      ...state.data!,
      items: [period, { ...previous, amount: "1200.00", row_version: 2 }],
    };
    view.rerender(<StaffPayPanel own={false} employeeId="own" />);
    expect(within(summary).getByText("Сумма за период").nextElementSibling?.textContent).toBe(
      payMoney("1200.00"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Изменить сумму" }));
    expect(screen.getByRole("dialog", { name: "Тестовая форма" })).toHaveTextContent(
      "Версия периода: 2",
    );
    state.data = { ...state.data!, items: [period, { ...previous, row_version: 3 }] };
    view.rerender(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.getByRole("dialog", { name: "Тестовая форма" })).toHaveTextContent(
      "Версия периода: 2",
    );
  });
  it("hides cached summary and histories on error and clears write intent when manage is lost", () => {
    const view = render(<StaffPayPanel own={false} employeeId="own" />);
    fireEvent.click(screen.getByRole("button", { name: "История выдач" }));
    fireEvent.click(screen.getByRole("button", { name: "Изменить сумму" }));
    expect(screen.getByRole("dialog", { name: "Тестовая история" })).toBeInTheDocument();
    state.isError = true;
    view.rerender(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Выбранный период зарплаты" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Выдачи за выбранный период" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Тестовая история" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Тестовая форма" })).toHaveTextContent(
      "Сведения недоступны",
    );
    state.manage = false;
    view.rerender(<StaffPayPanel own={false} employeeId="own" />);
    expect(screen.queryByRole("dialog", { name: "Тестовая форма" })).not.toBeInTheDocument();
  });
  it("requires reveal again after the personal account scope changes", () => {
    const view = render(<PersonalStaffPayPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Показать сведения" }));
    expect(screen.getByText(period.note)).toBeInTheDocument();
    state.scope = ["other", "tenant", null];
    view.rerender(<PersonalStaffPayPanel />);
    expect(screen.queryByText(period.note)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Показать сведения" })).toBeInTheDocument();
    expect(state.query).toHaveBeenLastCalledWith(true, null, 1, false);
  });
});
