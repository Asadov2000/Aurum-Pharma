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
    render(
      <PayForm
        intent={{ kind: "entry", period }}
        employeeId="employee"
        timezone="Asia/Dushanbe"
        onClose={close}
      />,
    );
    fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "200,50" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("button", { name: "Повторить запрос" });
    expect(screen.getByLabelText("Сумма выдачи, TJS")).toBeDisabled();
    const initial = commands.entry.mutateAsync.mock.calls[0][0];
    expect(initial.payload.amount).toBe("200.50");
    expect(initial.payload.operation_id).toMatch(/^[\da-f-]{36}$/);
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
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Сумма выдачи должна быть больше нуля")).toBeInTheDocument();
    expect(commands.entry.mutateAsync).not.toHaveBeenCalled();
  });
});
