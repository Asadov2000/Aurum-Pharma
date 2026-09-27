import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

vi.mock("@/features/auth/AccountIdentityPanel", () => ({
  AccountIdentityPanel: () => (
    <div data-testid="identity-panel">
      Способы входа
      <input aria-label="Черновик способа входа" defaultValue="" />
    </div>
  ),
}));
const listActiveSessions = vi.fn();
const revokeActiveSession = vi.fn();
const revokeOtherSessions = vi.fn();

vi.mock("@/features/auth/MfaSettingsPanel", () => ({
  MfaSettingsPanel: () => (
    <div data-testid="mfa-panel">
      Настройки двухфакторной защиты
      <input aria-label="Черновик настройки защиты" defaultValue="" />
    </div>
  ),
}));

vi.mock("@/features/roles/OwnershipTransferPanel", () => ({
  OwnershipTransferPanel: () => (
    <div data-testid="ownership-panel">
      <input aria-label="Черновик передачи владения" defaultValue="" />
    </div>
  ),
}));

vi.mock("@/features/auth/api", () => ({
  fetchMe: vi.fn(),
  listActiveSessions: (...args: unknown[]) => listActiveSessions(...args),
  revokeActiveSession: (...args: unknown[]) => revokeActiveSession(...args),
  revokeOtherSessions: (...args: unknown[]) => revokeOtherSessions(...args),
}));

import { SecurityPage } from "@/features/auth/SecurityPage";
import { activeSessionsQueryKey } from "@/features/auth/queries";
import { type ActiveSession } from "@/features/auth/types";

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

function renderPage(embedded = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <SecurityPage embedded={embedded} />
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

function pendingSessions() {
  let resolve!: (sessions: ActiveSession[]) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<ActiveSession[]>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
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

  it.each([false, true])(
    "shows one leading security heading and profile link (embedded=%s)",
    async (embedded) => {
      listActiveSessions.mockResolvedValue([CURRENT_SESSION, OTHER_SESSION]);
      renderPage(embedded);

      const page = screen.getByTestId("security-page");
      const heading = within(page).getByRole("heading", { name: "Безопасность" });
      expect(within(page).getAllByRole("heading", { name: "Безопасность" })).toHaveLength(1);
      expect(
        heading.compareDocumentPosition(screen.getByTestId("identity-panel")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        heading.compareDocumentPosition(screen.getByTestId("mfa-panel")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      if (!embedded) {
        expect(screen.getByRole("link", { name: "Мой профиль" })).toHaveAttribute(
          "href",
          "/profile",
        );
      }
      expect(
        await screen.findByRole("heading", { name: "Устройства и входы" }),
      ).toBeInTheDocument();
    },
  );

  it("shows current and other sessions with safe metadata", async () => {
    listActiveSessions.mockResolvedValueOnce([CURRENT_SESSION, OTHER_SESSION]);

    renderPage();

    expect(await screen.findByText("Windows · Chrome")).toBeInTheDocument();
    expect(screen.getByText("Android · Chrome")).toBeInTheDocument();
    expect(screen.getByText("Текущий сеанс")).toBeInTheDocument();
    expect(screen.getByText("203.0.x.x")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Завершить" })).toHaveLength(1);
  });

  it("does not offer session revocation when only the current device is active", async () => {
    listActiveSessions.mockResolvedValue([CURRENT_SESSION]);
    renderPage();

    await screen.findByText("Windows · Chrome");
    expect(screen.queryByRole("button", { name: "Завершить" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить остальные" })).toBeDisabled();
    expect(revokeActiveSession).not.toHaveBeenCalled();
    expect(revokeOtherSessions).not.toHaveBeenCalled();
  });

  it("revokes a selected non-current session after confirmation", async () => {
    listActiveSessions.mockResolvedValue([CURRENT_SESSION, OTHER_SESSION]);
    revokeActiveSession.mockResolvedValueOnce({ status: "ok", revoked_count: 1 });
    renderPage();
    await screen.findByText("Android · Chrome");

    fireEvent.click(screen.getByRole("button", { name: "Завершить" }));
    let dialog = screen.getByRole("dialog", { name: "Завершить сеанс?" });
    expect(revokeActiveSession).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(revokeActiveSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Завершить" }));
    dialog = screen.getByRole("dialog", { name: "Завершить сеанс?" });
    expect(revokeActiveSession).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Завершить" }));

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
    let dialog = screen.getByRole("dialog", { name: "Завершить остальные сеансы?" });
    expect(revokeOtherSessions).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(revokeOtherSessions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Завершить остальные" }));
    dialog = screen.getByRole("dialog", { name: "Завершить остальные сеансы?" });
    expect(revokeOtherSessions).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Завершить остальные" }));

    await waitFor(() => {
      expect(revokeOtherSessions).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("Завершено сеансов: 1.")).toBeInTheDocument();
  });

  it("hides cached devices after a refresh error and restores them on retry", async () => {
    listActiveSessions.mockResolvedValueOnce([CURRENT_SESSION, OTHER_SESSION]);
    const { queryClient } = renderPage();
    await screen.findByText("Android · Chrome");

    listActiveSessions.mockRejectedValueOnce(new Error("Request failed"));
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: activeSessionsQueryKey });
    });

    const retry = await screen.findByRole("button", { name: "Повторить" });
    expect(queryClient.getQueryData(activeSessionsQueryKey)).toEqual([
      CURRENT_SESSION,
      OTHER_SESSION,
    ]);
    expect(screen.queryByText("Windows · Chrome")).not.toBeInTheDocument();
    expect(screen.queryByText("Android · Chrome")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Завершить" })).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Завершить остальные" })).not.toBeInTheDocument();
    listActiveSessions.mockResolvedValueOnce([CURRENT_SESSION]);
    fireEvent.click(retry);

    expect(await screen.findByText("Windows · Chrome")).toBeInTheDocument();
    expect(screen.queryByText("Android · Chrome")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
    expect(listActiveSessions).toHaveBeenCalledTimes(3);
  });

  it("preserves child panel state throughout loading, error and recovery", async () => {
    const firstRequest = pendingSessions();
    listActiveSessions.mockReturnValueOnce(firstRequest.promise);
    renderPage();

    const page = screen.getByTestId("security-page");
    const drafts = [
      screen.getByRole("textbox", { name: "Черновик способа входа" }),
      screen.getByRole("textbox", { name: "Черновик настройки защиты" }),
      screen.getByRole("textbox", { name: "Черновик передачи владения" }),
    ];
    drafts.forEach((draft) =>
      fireEvent.change(draft, { target: { value: "Сохранённый черновик" } }),
    );

    await act(async () => {
      firstRequest.reject(new Error("Request failed"));
    });
    const retry = await screen.findByRole("button", { name: "Повторить" });
    expect(screen.getByTestId("security-page")).toBe(page);
    drafts.forEach((draft) => {
      expect(draft).toBeInTheDocument();
      expect(draft).toHaveValue("Сохранённый черновик");
    });

    const recovery = pendingSessions();
    listActiveSessions.mockReturnValueOnce(recovery.promise);
    fireEvent.click(retry);
    await waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));
    drafts.forEach((draft) => expect(draft).toBeInTheDocument());

    await act(async () => {
      recovery.resolve([CURRENT_SESSION]);
    });
    await screen.findByText("Windows · Chrome");
    expect(screen.getByTestId("security-page")).toBe(page);
    drafts.forEach((draft) => {
      expect(draft).toBeInTheDocument();
      expect(draft).toHaveValue("Сохранённый черновик");
    });
  });
});
