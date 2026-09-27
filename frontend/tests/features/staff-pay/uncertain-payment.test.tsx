import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { useAuthStore } from "@/stores/auth";
import { type StaffPayPeriod } from "@/features/staff-pay/types";

vi.mock("@/features/staff-pay/api", () => ({
  listStaffPay: vi.fn(),
  createStaffPayEntry: vi.fn(),
  createStaffPayPeriod: vi.fn(),
  updateStaffPayPeriod: vi.fn(),
  reverseStaffPayEntry: vi.fn(),
}));
import { createStaffPayEntry, listStaffPay } from "@/features/staff-pay/api";
import { StaffPayPanel } from "@/features/staff-pay/StaffPayPanel";

const original = useAuthStore.getState();
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
afterEach(() => {
  cleanup();
  client.clear();
  useAuthStore.setState({
    user: original.user,
    accessToken: original.accessToken,
    hydrated: original.hydrated,
  });
});

it("keeps the identical cash intent through lost POST and failed background GET until recovery", async () => {
  const owner: MeResponse = {
    id: "owner",
    home_tenant_id: "tenant",
    active_tenant_id: "tenant",
    email: "synthetic@example.test",
    full_name: "Synthetic",
    is_developer: false,
    is_administrator: false,
    is_tenant_owner: true,
    status: "active",
    last_login_at: null,
    level: 3,
    branch_assignments: {},
    permissions: [],
    platform_capabilities: [],
    support_access: null,
  };
  useAuthStore.setState({ user: owner, accessToken: "synthetic", hydrated: true });
  const period: StaffPayPeriod = {
    id: "period",
    employee_id: "employee",
    period_from: "2026-09-01",
    period_to: "2026-09-27",
    amount: "1000.00",
    paid_amount: "0",
    advance_amount: "0",
    remaining_amount: "1000.00",
    currency: "TJS",
    note: "",
    row_version: 1,
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
  };
  const data = { items: [period], total: 1, page: 1, page_size: 10, timezone: "Asia/Dushanbe" };
  vi.mocked(listStaffPay)
    .mockResolvedValueOnce(data)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue(data);
  vi.mocked(createStaffPayEntry)
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({
      id: "entry",
      kind: "payment",
      amount: "200.50",
      paid_on: "2026-09-27",
      note: "",
      is_reversed: false,
      reverses_entry_id: null,
      created_at: "2026-09-27T00:00:00Z",
    });
  render(
    <QueryClientProvider client={client}>
      <StaffPayPanel own={false} employeeId="employee" />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Записать выдачу" }));
  fireEvent.change(screen.getByLabelText("Сумма выдачи, TJS"), { target: { value: "200,50" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByRole("button", { name: "Повторить запрос" });
  await waitFor(() => expect(listStaffPay).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Сумма выдачи, TJS")).toHaveValue("200,50");
  expect(screen.getByLabelText("Сумма выдачи, TJS")).toBeDisabled();
  const first = vi.mocked(createStaffPayEntry).mock.calls[0];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["staff-pay"] });
  });
  expect(screen.getByLabelText("Сумма выдачи, TJS")).toHaveValue("200,50");
  fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Записать выдачу" })).not.toBeInTheDocument(),
  );
  expect(vi.mocked(createStaffPayEntry).mock.calls[1]).toEqual(first);
  expect(first[1].amount).toBe("200.50");
});
