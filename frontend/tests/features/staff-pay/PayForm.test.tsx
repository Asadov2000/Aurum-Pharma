import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const commands = vi.hoisted(() => ({
  create: { isPending: false, mutateAsync: vi.fn() },
  update: { isPending: false, mutateAsync: vi.fn() },
  entry: { isPending: false, mutateAsync: vi.fn() },
  reverse: { isPending: false, mutateAsync: vi.fn() },
}));
vi.mock("@/features/staff-pay/queries", () => ({ useStaffPayCommands: () => commands }));
import { PayForm } from "@/features/staff-pay/PayForm";
import { type StaffPayPeriod } from "@/features/staff-pay/types";

const period: StaffPayPeriod = {
  id: "period",
  employee_id: "employee",
  period_from: "2026-09-01",
  period_to: "2026-09-27",
  amount: "1000.00",
  paid_amount: "500.00",
  advance_amount: "200.00",
  remaining_amount: "500.00",
  currency: "TJS",
  note: "",
  row_version: 4,
  created_at: "2026-09-27T00:00:00Z",
  updated_at: "2026-09-27T00:00:00Z",
};
describe("manual salary commands", () => {
  beforeEach(() => Object.values(commands).forEach((command) => command.mutateAsync.mockReset()));
  it("retries an uncertain payment with identical payload and operation id", async () => {
    commands.entry.mutateAsync
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValueOnce(period);
    const close = vi.fn();
    const view = render(
      <PayForm
        intent={{ kind: "entry", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={close}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "200,50" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("button", { name: "Повторить запрос" });
    expect(screen.getByLabelText("Сумма выдачи, TJS")).toBeDisabled();
    const initial = commands.entry.mutateAsync.mock.calls[0][0];
    expect(initial.payload.amount).toBe("200.50");
    expect(initial.payload.operation_id).toMatch(/^[\da-f-]{36}$/);
    view.rerender(
      <PayForm
        intent={{ kind: "entry", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={close}
        contextUnavailable
      />,
    );
    expect(screen.queryByText("Сумма за период")).not.toBeInTheDocument();
    expect(screen.queryByText(/Остаток после записи:/)).not.toBeInTheDocument();
    expect(
      screen.getByText("Нет актуальных данных периода. Остаток после записи недоступен."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(commands.entry.mutateAsync.mock.calls[1][0]).toEqual(initial);
  });
  it("sends captured version without changing employee or period dates on editing", async () => {
    commands.update.mutateAsync.mockResolvedValue(period);
    render(
      <PayForm
        intent={{ kind: "edit", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма за период, TJS"), { target: { value: "1200" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(commands.update.mutateAsync).toHaveBeenCalledWith({
        id: "period",
        payload: { amount: "1200", note: "", expected_row_version: 4 },
      }),
    );
  });
  it("rejects zero cash payment without sending a command", async () => {
    render(
      <PayForm
        intent={{ kind: "entry", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Сумма выдачи должна быть больше нуля")).toBeInTheDocument();
    expect(commands.entry.mutateAsync).not.toHaveBeenCalled();
  });
  it("requires confirmation of cash already handed out before submitting an entry", async () => {
    commands.entry.mutateAsync.mockResolvedValue(period);
    render(
      <PayForm
        intent={{ kind: "entry", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(
      await screen.findByText("Подтвердите, что наличные уже выданы сотруднику"),
    ).toBeInTheDocument();
    expect(commands.entry.mutateAsync).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(commands.entry.mutateAsync).toHaveBeenCalledOnce());
    expect(commands.entry.mutateAsync.mock.calls[0][0].payload).not.toHaveProperty(
      "cash_confirmed",
    );
  });
  it.each(["payment", "advance"])(
    "previews %s once and permits an overpayment without describing employee debt",
    async (kind) => {
      commands.entry.mutateAsync.mockResolvedValue(period);
      render(
        <PayForm
          intent={{ kind: "entry", period }}
          employeeId="employee"
          timezone="Asia/Dushanbe"
          onClose={vi.fn()}
        />,
      );
      fireEvent.change(screen.getByLabelText("Вид выдачи"), { target: { value: kind } });
      fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "200,50" } });
      expect(screen.getByText("Остаток после записи: 299,50 TJS")).toBeInTheDocument();
      expect(screen.getByText("01.09.2026 — 27.09.2026")).toBeInTheDocument();
      expect(
        screen.getByText("Предварительный расчёт по загруженным данным. Итог подтвердит сервер."),
      ).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "500,01" } });
      expect(
        screen.getByText(
          "После записи: выдано сверх суммы 0,01 TJS. Это не создаёт долг сотрудника.",
        ),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" }));
      fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
      await waitFor(() => expect(commands.entry.mutateAsync).toHaveBeenCalledOnce());
      expect(commands.entry.mutateAsync.mock.calls[0][0].payload).toMatchObject({
        amount: "500.01",
        kind,
      });
    },
  );
  it("keeps cents exact at the money limit and hides previews for invalid input", () => {
    render(
      <PayForm
        intent={{
          kind: "entry",
          period: { ...period, amount: "999999999999.99", paid_amount: "999999999999.97" },
        }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "0,01" } });
    expect(screen.getByText("Остаток после записи: 0,01 TJS")).toBeInTheDocument();
    for (const value of ["0,001", "12abc", "", "0"]) {
      fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value } });
      expect(
        screen.getByText("Введите сумму выдачи, чтобы увидеть остаток после записи."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Остаток после записи:/)).not.toBeInTheDocument();
    }
  });
  it("creates a manual period without requiring cash confirmation", async () => {
    commands.create.mutateAsync.mockResolvedValue(period);
    render(
      <PayForm
        intent={{ kind: "create" }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сумма за период, TJS"), { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(commands.create.mutateAsync).toHaveBeenCalledOnce());
  });
  it("blocks a fresh cash entry until current period data recovers", async () => {
    commands.entry.mutateAsync.mockResolvedValue(period);
    const props = {
      intent: { kind: "entry" as const, period },
      employeeId: "employee",
      timezone: "Asia/Dushanbe",
      onClose: vi.fn(),
    };
    const view = render(<PayForm {...props} contextUnavailable />);
    expect(screen.getByLabelText("Сумма выдачи, TJS")).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(screen.queryByText("Сумма за период")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(commands.entry.mutateAsync).not.toHaveBeenCalled();
    view.rerender(<PayForm {...props} />);
    expect(screen.getByLabelText("Сумма выдачи, TJS")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Наличные уже выданы сотруднику" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(commands.entry.mutateAsync).toHaveBeenCalledOnce());
  });
});
