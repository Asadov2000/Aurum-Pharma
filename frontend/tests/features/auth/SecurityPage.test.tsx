import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";

const listActiveSessions = vi.fn();
const revokeActiveSession = vi.fn();
const revokeOtherSessions = vi.fn();

vi.mock("@/features/auth/MfaSettingsPanel", () => ({
  MfaSettingsPanel: () => <div>Настройки двухфакторной защиты</div>,
}));

vi.mock("@/features/auth/api", () => ({
  fetchMe: vi.fn(),
  listActiveSessions: (...args: unknown[]) => listActiveSessions(...args),
  revokeActiveSession: (...args: unknown[]) => revokeActiveSession(...args),
  revokeOtherSessions: (...args: unknown[]) => revokeOtherSessions(...args),
}));

import { SecurityPage } from "@/features/auth/SecurityPage";

const CURRENT_SESSION = {
  id: "11111111-1111-4111-8111-111111111111",
  user_agent: "Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
  ip_address: "203.0.x.x",
  created_at: "2026-07-18T08:00:00Z",
  last_used_at: "2026-07-19T08:00:00Z",
  expires_at: "2026-08-02T08:00:00Z",
  is_current: true,
};

const OTHER_SESSION = {
  id: "22222222-2222-4222-8222-222222222222",
  user_agent: "Mozilla/5.0 (Linux; Android 14) Chrome/126.0",
  ip_address: "198.51.x.x",
  created_at: "2026-07-17T08:00:00Z",
  last_used_at: "2026-07-18T09:00:00Z",
  expires_at: "2026-08-01T08:00:00Z",
  is_current: false,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SecurityPage />
    </QueryClientProvider>,
  );
}

describe("SecurityPage", () => {
  beforeEach(() => {
    listActiveSessions.mockReset();
    revokeActiveSession.mockReset();
    revokeOtherSessions.mockReset();
    useAuthStore.getState().setTokens({
      access_token: "test-access-token",
      token_type: "bearer",
      expires_in: 900,
    });
  });

  afterEach(() => {
    act(() => useAuthStore.getState().clear());
    vi.clearAllMocks();
  });

  it("shows current and other sessions with safe metadata", async () => {
    listActiveSessions.mockResolvedValueOnce([CURRENT_SESSION, OTHER_SESSION]);

    renderPage();

    expect(await screen.findByText("Windows · Chrome")).toBeInTheDocument();
    expect(screen.getByText("Android · Chrome")).toBeInTheDocument();
    expect(screen.getByText("Текущий сеанс")).toBeInTheDocument();
    expect(screen.getByText("203.0.x.x")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Завершить" })).toHaveLength(1);
  });

  it("revokes a selected non-current session after confirmation", async () => {
    listActiveSessions.mockResolvedValue([CURRENT_SESSION, OTHER_SESSION]);
    revokeActiveSession.mockResolvedValueOnce({ status: "ok", revoked_count: 1 });
    renderPage();
    await screen.findByText("Android · Chrome");

    fireEvent.click(screen.getByRole("button", { name: "Завершить" }));
    expect(screen.getByText("Завершить сеанс?")).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole("button", { name: "Завершить" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => {
      expect(revokeActiveSession).toHaveBeenCalledWith(OTHER_SESSION.id);
    });
    expect(await screen.findByText("Сеанс завершён.")).toBeInTheDocument();
  });

  it("revokes every session except the current one", async () => {
    listActiveSessions.mockResolvedValue([CURRENT_SESSION, OTHER_SESSION]);
    revokeOtherSessions.mockResolvedValueOnce({ status: "ok", revoked_count: 1 });
    renderPage();
    await screen.findByText("Android · Chrome");

    fireEvent.click(screen.getByRole("button", { name: "Завершить остальные" }));
    const confirmButtons = screen.getAllByRole("button", { name: "Завершить остальные" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => {
      expect(revokeOtherSessions).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("Завершено сеансов: 1.")).toBeInTheDocument();
  });
});
