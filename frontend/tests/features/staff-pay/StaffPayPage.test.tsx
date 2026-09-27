import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type StaffPayEmployee, type StaffPayList } from "@/features/staff-pay/types";

const employees: StaffPayEmployee[] = [
  { id: "first", full_name: "Первый сотрудник", status: "active" },
  { id: "second", full_name: "Второй сотрудник", status: "suspended" },
];
const state = vi.hoisted(() => ({
  access: { view: true, manage: true, scope: ["owner", "tenant", null] },
  data: undefined as StaffPayList<StaffPayEmployee> | undefined,
  isPending: false,
  isError: false,
  error: new Error("offline"),
  refetch: vi.fn(),
  employeesQuery: vi.fn(),
}));
vi.mock("@/features/staff-pay/queries", () => ({
  useStaffPayAccess: () => state.access,
  useStaffPayEmployees: (...args: unknown[]) => {
    state.employeesQuery(...args);
    return state;
  },
}));
vi.mock("@/features/staff-pay/StaffPayPanel", () => ({
  StaffPayPanel: ({
    own,
    employeeId,
    contextUnavailable,
  }: {
    own: boolean;
    employeeId: string;
    contextUnavailable?: boolean;
  }) => {
    const [opened, setOpened] = useState(false);
    return (
      <div>
        {contextUnavailable ? <p>Контекст недоступен</p> : <p>Сведения: {employeeId}</p>}
        <p>{own ? "Собственное чтение" : "Управление"}</p>
        <button onClick={() => setOpened(true)}>Открыть форму</button>
        {opened && !contextUnavailable ? <p>Открытая форма: {employeeId}</p> : null}
      </div>
    );
  },
}));
vi.mock("@/features/staff-pay/PayHistory", () => ({
  ReadError: () => <p role="alert">Не удалось загрузить сведения</p>,
}));
import { StaffPayPage } from "@/features/staff-pay/StaffPayPage";

describe("staff pay employee selection", () => {
  beforeEach(() => {
    state.access = { view: true, manage: true, scope: ["owner", "tenant", null] };
    state.data = {
      items: employees,
      total: employees.length,
      page: 1,
      page_size: 10,
      timezone: "Asia/Dushanbe",
    };
    state.isPending = false;
    state.isError = false;
    state.employeesQuery.mockClear();
  });

  it("rejects access before querying employees", () => {
    state.access.view = false;
    render(<StaffPayPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Нет доступа");
    expect(state.employeesQuery).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Найти сотрудника")).not.toBeInTheDocument();
  });

  it("starts without personal details and remounts them when selecting another employee", () => {
    render(<StaffPayPage />);
    expect(screen.getByRole("heading", { name: "Выберите сотрудника" })).toBeInTheDocument();
    expect(screen.queryByText(/Сведения:/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Первый сотрудник" }));
    expect(screen.getByRole("heading", { name: "Первый сотрудник" })).toBeInTheDocument();
    expect(screen.getByText("Сведения: first")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть форму" }));
    expect(screen.getByText("Открытая форма: first")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Второй сотрудник/ }));
    expect(screen.getByText("Сведения: second")).toBeInTheDocument();
    expect(screen.queryByText(/Открытая форма:/)).not.toBeInTheDocument();
    expect(screen.queryByText("Сведения: first")).not.toBeInTheDocument();
  });

  it("searches on submission and clears the selected employee", async () => {
    render(<StaffPayPage />);
    fireEvent.click(screen.getByRole("button", { name: "Первый сотрудник" }));
    fireEvent.change(screen.getByLabelText("Найти сотрудника"), {
      target: { value: "  Второй  " },
    });
    expect(state.employeesQuery).toHaveBeenLastCalledWith("", 1);
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(state.employeesQuery).toHaveBeenLastCalledWith("Второй", 1));
    expect(screen.queryByText("Сведения: first")).not.toBeInTheDocument();
  });

  it("hides cached employees and selected salary details after a failed refresh", () => {
    const view = render(<StaffPayPage />);
    fireEvent.click(screen.getByRole("button", { name: "Первый сотрудник" }));
    fireEvent.click(screen.getByRole("button", { name: "Открыть форму" }));
    state.isError = true;
    view.rerender(<StaffPayPage />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Сведения: first")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Первый сотрудник" })).not.toBeInTheDocument();
    expect(screen.getByText("Контекст недоступен")).toBeInTheDocument();
    expect(screen.queryByText("Открытая форма: first")).not.toBeInTheDocument();
    state.isError = false;
    view.rerender(<StaffPayPage />);
    expect(screen.getByText("Открытая форма: first")).toBeInTheDocument();
  });

  it("clears selected details and search when account or tenant scope changes", async () => {
    const view = render(<StaffPayPage />);
    fireEvent.change(screen.getByLabelText("Найти сотрудника"), { target: { value: "Первый" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(state.employeesQuery).toHaveBeenLastCalledWith("Первый", 1));
    fireEvent.click(screen.getByRole("button", { name: "Первый сотрудник" }));
    state.access.scope = ["other-owner", "other-tenant", null];
    view.rerender(<StaffPayPage />);
    expect(screen.queryByText("Сведения: first")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Найти сотрудника")).toHaveValue("");
    expect(state.employeesQuery).toHaveBeenLastCalledWith("", 1);
  });
});
