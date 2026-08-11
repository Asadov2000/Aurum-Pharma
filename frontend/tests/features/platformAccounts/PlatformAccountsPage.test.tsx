import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listPlatformStaffAccounts = vi.fn();
const invitePlatformStaffAccount = vi.fn();
const mutatePlatformStaffAccount = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    id: "current-user",
    platform_capabilities: ["platform.accounts.view", "platform.accounts.manage"] as string[],
  },
}));

vi.mock("@/features/platformAccounts/api", () => ({
  listPlatformStaffAccounts: (...args: unknown[]) => listPlatformStaffAccounts(...args),
  invitePlatformStaffAccount: (...args: unknown[]) => invitePlatformStaffAccount(...args),
  mutatePlatformStaffAccount: (...args: unknown[]) => mutatePlatformStaffAccount(...args),
  activatePlatformStaffAccount: vi.fn(),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authState,
}));

import { PlatformAccountsPage } from "@/features/platformAccounts/PlatformAccountsPage";

const ACCOUNT = {
  user_id: "account-1",
  email: "candidate@aurum.tj",
  full_name: "Новый сотрудник",
  status: "invited" as const,
  version: 1,
  invited_at: "2026-08-11T10:00:00Z",
  invitation_expires_at: "2026-08-12T10:00:00Z",
  activated_at: null,
  blocked_at: null,
  offboarded_at: null,
  created_at: "2026-08-11T10:00:00Z",
  updated_at: "2026-08-11T10:00:00Z",
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformAccountsPage />
    </QueryClientProvider>,
  );
}

describe("PlatformAccountsPage", () => {
  beforeEach(() => {
    listPlatformStaffAccounts.mockReset();
    invitePlatformStaffAccount.mockReset();
    mutatePlatformStaffAccount.mockReset();
    authState.user.id = "current-user";
    authState.user.platform_capabilities = ["platform.accounts.view", "platform.accounts.manage"];
    listPlatformStaffAccounts.mockResolvedValue({ items: [ACCOUNT], total: 1 });
  });

  it("shows account state without any platform role assignment controls", async () => {
    renderPage();

    expect(await screen.findByText("Новый сотрудник")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("Ожидает активации")).toBeInTheDocument();
    expect(screen.queryByText(/назначить администратора/i)).not.toBeInTheDocument();
  });

  it("hides invitations for view-only grants", async () => {
    authState.user.platform_capabilities = ["platform.accounts.view"];
    renderPage();

    expect(await screen.findByText("Новый сотрудник")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Пригласить сотрудника" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Действия" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Действия с аккаунтом/ })).not.toBeInTheDocument();
  });

  it("shows only lifecycle actions allowed by each account status", async () => {
    listPlatformStaffAccounts.mockResolvedValue({
      items: [
        ACCOUNT,
        { ...ACCOUNT, user_id: "active-1", full_name: "Активный", status: "active" },
        { ...ACCOUNT, user_id: "blocked-1", full_name: "Заблокированный", status: "blocked" },
        { ...ACCOUNT, user_id: "offboarded-1", full_name: "Уволенный", status: "offboarded" },
      ],
      total: 4,
    });
    renderPage();

    await screen.findByText("Активный");
    fireEvent.click(screen.getByRole("button", { name: "Действия с аккаунтом Новый сотрудник" }));
    expect(
      await screen.findByRole("menuitem", { name: "Отправить приглашение повторно" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Вывести из команды" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Заблокировать" })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Действия с аккаунтом Активный" }));
    expect(await screen.findByRole("menuitem", { name: "Заблокировать" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Разблокировать" })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Действия с аккаунтом Заблокированный" }));
    expect(await screen.findByRole("menuitem", { name: "Разблокировать" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Заблокировать" })).not.toBeInTheDocument();

    expect(
      screen.queryByRole("button", { name: "Действия с аккаунтом Уволенный" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer lifecycle actions for the current account", async () => {
    authState.user.id = ACCOUNT.user_id;
    renderPage();

    expect(await screen.findByText("Новый сотрудник")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Действия с аккаунтом/ })).not.toBeInTheDocument();
  });

  it("sends the current version and a stable UUID with the lifecycle request", async () => {
    const operationId = "123e4567-e89b-42d3-a456-426614174000";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(operationId);
    mutatePlatformStaffAccount.mockResolvedValue({
      ...ACCOUNT,
      version: 2,
      activation_token: "replacement-token",
    });
    renderPage();
    await screen.findByText("Новый сотрудник");

    fireEvent.click(screen.getByRole("button", { name: "Действия с аккаунтом Новый сотрудник" }));
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Отправить приглашение повторно" }),
    );
    fireEvent.change(screen.getByLabelText("Комментарий"), {
      target: { value: "Сотрудник не получил исходное приглашение" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать новую ссылку" }));

    await waitFor(() => {
      expect(mutatePlatformStaffAccount).toHaveBeenCalledWith("reinvite", ACCOUNT.user_id, {
        version: ACCOUNT.version,
        operation_id: operationId,
        reason_code: "invitation_delivery",
        reason: "Сотрудник не получил исходное приглашение",
      });
    });
    expect(await screen.findByLabelText("Ссылка активации")).toHaveValue(
      "http://localhost:3000/activate-platform#token=replacement-token",
    );
  });

  it("closes the action and refreshes the list after a version conflict", async () => {
    mutatePlatformStaffAccount.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409 },
    });
    renderPage();
    await screen.findByText("Новый сотрудник");

    fireEvent.click(screen.getByRole("button", { name: "Действия с аккаунтом Новый сотрудник" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Вывести из команды" }));
    fireEvent.change(screen.getByLabelText("Комментарий"), {
      target: { value: "Трудовые отношения завершены сегодня" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Вывести из команды" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Аккаунт уже изменился");
    expect(
      screen.queryByRole("dialog", { name: "Вывести сотрудника из команды" }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(listPlatformStaffAccounts).toHaveBeenCalledTimes(2));
  });

  it("creates an unprivileged invitation and reveals its local activation link", async () => {
    invitePlatformStaffAccount.mockResolvedValue({
      ...ACCOUNT,
      activation_token: "one-time-token",
    });
    renderPage();
    await screen.findByText("Новый сотрудник");

    fireEvent.click(screen.getByRole("button", { name: "Пригласить сотрудника" }));
    fireEvent.change(screen.getByLabelText("Имя сотрудника"), {
      target: { value: "Второй сотрудник" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "second@aurum.tj" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать приглашение" }));

    await waitFor(() => {
      expect(invitePlatformStaffAccount).toHaveBeenCalledWith({
        full_name: "Второй сотрудник",
        email: "second@aurum.tj",
      });
    });
    expect(await screen.findByLabelText("Ссылка активации")).toHaveValue(
      "http://localhost:3000/activate-platform#token=one-time-token",
    );
  });

  it("keeps the activation link visible when clipboard access is denied", async () => {
    invitePlatformStaffAccount.mockResolvedValue({
      ...ACCOUNT,
      activation_token: "one-time-token",
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("clipboard denied")) },
    });
    renderPage();
    await screen.findByText("Новый сотрудник");

    fireEvent.click(screen.getByRole("button", { name: "Пригласить сотрудника" }));
    fireEvent.change(screen.getByLabelText("Имя сотрудника"), {
      target: { value: "Второй сотрудник" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "second@aurum.tj" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать приглашение" }));
    await screen.findByLabelText("Ссылка активации");

    fireEvent.click(screen.getByRole("button", { name: "Копировать ссылку" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("скопируйте её вручную");
    expect(screen.getByLabelText("Ссылка активации")).toBeVisible();
  });
});
