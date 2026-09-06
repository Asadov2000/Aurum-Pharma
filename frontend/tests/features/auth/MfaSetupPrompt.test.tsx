import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { type MeResponse } from "@/features/auth/types";

const fetchMfaSettings = vi.fn();
const dismissMfaPrompt = vi.fn();
vi.mock("@/features/auth/accountSecurityApi", () => ({
  fetchMfaSettings: (...args: unknown[]) => fetchMfaSettings(...args),
  dismissMfaPrompt: (...args: unknown[]) => dismissMfaPrompt(...args),
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, onClick }: { children: ReactNode; onClick: () => void }) => (
    <a href="/settings?section=security" onClick={onClick}>
      {children}
    </a>
  ),
}));
import { MfaSetupPrompt } from "@/features/auth/MfaSetupPrompt";

describe("optional first MFA suggestion", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useAuthStore
      .getState()
      .setTokens({ access_token: "current", token_type: "bearer", expires_in: 900 });
    useAuthStore.getState().setUser({ id: "user-one" } as MeResponse);
    fetchMfaSettings.mockResolvedValue({
      enabled: false,
      prompt_pending: true,
      has_password: true,
    });
    dismissMfaPrompt.mockResolvedValue(undefined);
  });
  afterEach(() => act(() => useAuthStore.getState().clear()));

  function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
    return render(
      <QueryClientProvider client={client}>
        <MfaSetupPrompt />
      </QueryClientProvider>,
    );
  }

  it("offers setup without a modal and remembers dismissal across fresh loads", async () => {
    const view = mount();
    expect(await screen.findByRole("link", { name: "Настроить" })).toHaveAttribute(
      "href",
      "/settings?section=security",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Пока пропустить" }));
    await waitFor(() =>
      expect(screen.queryByText("Усилить защиту аккаунта?")).not.toBeInTheDocument(),
    );
    expect(dismissMfaPrompt).toHaveBeenCalledTimes(1);
    view.unmount();
    fetchMfaSettings.mockResolvedValue({
      enabled: false,
      prompt_pending: false,
      has_password: true,
    });
    mount();
    await waitFor(() => expect(fetchMfaSettings).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Усилить защиту аккаунта?")).not.toBeInTheDocument();
  });

  it("does not suggest enabling an already enabled factor", async () => {
    fetchMfaSettings.mockResolvedValue({ enabled: true, prompt_pending: true, has_password: true });
    mount();
    await waitFor(() => expect(fetchMfaSettings).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps retry available if dismissal cannot be saved", async () => {
    dismissMfaPrompt.mockRejectedValue(new Error("offline"));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Пока пропустить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Можно продолжить работу");
    expect(screen.getByRole("button", { name: "Пока пропустить" })).toBeEnabled();
  });
});
