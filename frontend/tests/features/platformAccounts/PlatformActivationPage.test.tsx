import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const activatePlatformStaffAccount = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({ token: "activation-token" }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

vi.mock("@/features/platformAccounts/api", () => ({
  activatePlatformStaffAccount: (...args: unknown[]) => activatePlatformStaffAccount(...args),
  listPlatformStaffAccounts: vi.fn(),
  invitePlatformStaffAccount: vi.fn(),
}));

import { PlatformActivationPage } from "@/features/platformAccounts/PlatformActivationPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformActivationPage />
    </QueryClientProvider>,
  );
}

describe("PlatformActivationPage", () => {
  beforeEach(() => {
    activatePlatformStaffAccount.mockReset();
    activatePlatformStaffAccount.mockResolvedValue(undefined);
    window.history.replaceState({}, "", "/activate-platform?token=activation-token");
  });

  it("validates matching strong passwords and consumes the token from memory", async () => {
    renderPage();
    expect(window.location.search).toBe("");

    fireEvent.change(screen.getByLabelText("Новый пароль"), {
      target: { value: "StrongPlatform9Password" },
    });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), {
      target: { value: "StrongPlatform9Password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Активировать аккаунт" }));

    await waitFor(() => {
      expect(activatePlatformStaffAccount).toHaveBeenCalledWith({
        token: "activation-token",
        password: "StrongPlatform9Password",
      });
    });
    expect(await screen.findByText("Аккаунт активирован")).toBeInTheDocument();
  });
});
