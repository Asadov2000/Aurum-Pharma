import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listPlatformStaffAccounts = vi.fn();
const invitePlatformStaffAccount = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    platform_capabilities: ["platform.accounts.view", "platform.accounts.manage"] as string[],
  },
}));

vi.mock("@/features/platformAccounts/api", () => ({
  listPlatformStaffAccounts: (...args: unknown[]) => listPlatformStaffAccounts(...args),
  invitePlatformStaffAccount: (...args: unknown[]) => invitePlatformStaffAccount(...args),
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
      "http://localhost:3000/activate-platform?token=one-time-token",
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
