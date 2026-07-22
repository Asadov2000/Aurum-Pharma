import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  activate: vi.fn(),
  mutate: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock("@/features/supportAccess/context", () => ({
  activateSupportContext: (...args: unknown[]) => mocks.activate(...args),
}));

vi.mock("@/features/supportAccess/queries", () => ({
  useSupportCapabilities: () => ({
    data: [
      { code: "users.view" },
      { code: "branches.view" },
      { code: "roles.create" },
      { code: "roles.update" },
      { code: "roles.assign" },
      { code: "users.block" },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useStartSupportSession: () => ({ mutateAsync: mocks.mutate }),
}));

import { SupportAccessForm } from "@/features/supportAccess/SupportAccessForm";

const SESSION = {
  id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "22222222-2222-4222-8222-222222222222",
  tenant_name: "Шифо",
  actor_user_id: "33333333-3333-4333-8333-333333333333",
  reason: "Настройка ролей перед запуском",
  capabilities: ["users.view", "branches.view", "roles.create", "roles.update", "roles.assign"],
  is_read_only: false,
  started_at: "2026-07-22T10:00:00Z",
  expires_at: "2026-07-22T10:15:00Z",
  revoked_at: null,
};

describe("SupportAccessForm", () => {
  beforeEach(() => {
    mocks.activate.mockReset().mockResolvedValue(undefined);
    mocks.mutate.mockReset().mockResolvedValue(SESSION);
    mocks.navigate.mockReset().mockResolvedValue(undefined);
  });

  it("opens an exact role-management scope with a reason and expiry", async () => {
    const onClose = vi.fn();
    const onPendingChange = vi.fn();
    render(
      <SupportAccessForm
        tenantId={SESSION.tenant_id}
        tenantName="Шифо"
        onClose={onClose}
        onPendingChange={onPendingChange}
      />,
    );

    fireEvent.click(screen.getByLabelText("Роли и назначения"));
    fireEvent.change(screen.getByLabelText("Причина доступа"), {
      target: { value: "Настройка ролей перед запуском" },
    });
    fireEvent.change(screen.getByLabelText("Срок"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Открыть доступ" }));

    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledTimes(1));
    expect(mocks.mutate).toHaveBeenCalledWith({
      tenant_id: SESSION.tenant_id,
      reason: "Настройка ролей перед запуском",
      duration_minutes: 10,
      capabilities: ["users.view", "branches.view", "roles.create", "roles.update", "roles.assign"],
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mocks.activate).toHaveBeenCalledWith(SESSION);
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/roles" });
    expect(onPendingChange.mock.calls.map(([pending]) => pending)).toEqual([true, false]);
  });

  it("does not start a session without a meaningful reason", async () => {
    render(<SupportAccessForm tenantId={SESSION.tenant_id} tenantName="Шифо" onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText("Причина доступа"), {
      target: { value: "мало" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Открыть доступ" }));

    expect(
      await screen.findByText("Укажите причину подробнее, минимум 10 символов"),
    ).toBeInTheDocument();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("opens a separate account-security scope", async () => {
    render(<SupportAccessForm tenantId={SESSION.tenant_id} tenantName="Шифо" onClose={() => {}} />);

    fireEvent.click(screen.getByLabelText("Безопасность"));
    fireEvent.change(screen.getByLabelText("Причина доступа"), {
      target: { value: "Завершение подозрительных сеансов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Открыть доступ" }));

    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledTimes(1));
    expect(mocks.mutate).toHaveBeenCalledWith({
      tenant_id: SESSION.tenant_id,
      reason: "Завершение подозрительных сеансов",
      duration_minutes: 15,
      capabilities: ["users.view", "users.block"],
    });
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/users" });
  });

  it("disables cancellation while the support session is being opened", async () => {
    let resolveRequest: (session: typeof SESSION) => void = () => {};
    mocks.mutate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const onPendingChange = vi.fn();
    render(
      <SupportAccessForm
        tenantId={SESSION.tenant_id}
        tenantName="Шифо"
        onClose={() => {}}
        onPendingChange={onPendingChange}
      />,
    );

    fireEvent.change(screen.getByLabelText("Причина доступа"), {
      target: { value: "Проверка ролей перед запуском" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Открыть доступ" }));

    await waitFor(() => expect(onPendingChange).toHaveBeenCalledWith(true));
    expect(screen.getByRole("button", { name: "Отмена" })).toBeDisabled();

    await act(async () => resolveRequest(SESSION));
    await waitFor(() => expect(onPendingChange).toHaveBeenLastCalledWith(false));
  });
});
