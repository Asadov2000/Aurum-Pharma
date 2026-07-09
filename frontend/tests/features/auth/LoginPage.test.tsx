import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => ({}),
}));

const requestLoginCode = vi.fn();
const verifyLoginCode = vi.fn();

vi.mock("@/features/auth/api", () => ({
  requestLoginCode: (...args: unknown[]) => requestLoginCode(...args),
  verifyLoginCode: (...args: unknown[]) => verifyLoginCode(...args),
  logoutRequest: vi.fn(),
  refreshTokensRequest: vi.fn(),
  fetchMe: vi.fn(),
}));

import { LoginPage } from "@/features/auth/LoginPage";
import { useAuthStore } from "@/stores/auth";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LoginPage />
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    requestLoginCode.mockReset();
    verifyLoginCode.mockReset();
    useAuthStore.getState().clear();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("rejects an empty email and does not call the API", async () => {
    renderPage();
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    expect(await screen.findByText(/Введите email/i)).toBeInTheDocument();
    expect(requestLoginCode).not.toHaveBeenCalled();
  });

  it("advances to code step on successful email submit", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: null });
    renderPage();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@aurum.tj" } });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    expect(await screen.findByLabelText(/Код из письма/i)).toBeInTheDocument();
    expect(requestLoginCode).toHaveBeenCalledWith({ email: "owner@aurum.tj" });
  });

  it("auto-fills code input from dev_code in the response", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: "654321" });
    renderPage();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "dev@aurum.tj" } });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const codeInput = (await screen.findByLabelText(/Код из письма/i)) as HTMLInputElement;
    expect(codeInput.value).toBe("654321");
    expect(screen.getByText(/Dev-режим/i)).toBeInTheDocument();
  });

  it("logs in and navigates home on valid code", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: null });
    verifyLoginCode.mockResolvedValueOnce({
      access_token: "A",
      token_type: "bearer",
      expires_in: 900,
    });
    renderPage();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@aurum.tj" } });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const codeInput = await screen.findByLabelText(/Код из письма/i);
    fireEvent.change(codeInput, { target: { value: "123456" } });
    fireEvent.submit(codeInput.closest("form")!);
    await waitFor(() => {
      expect(verifyLoginCode).toHaveBeenCalledWith({
        email: "owner@aurum.tj",
        code: "123456",
        password: undefined,
      });
      expect(navigate).toHaveBeenCalledWith({ to: "/" });
      expect(useAuthStore.getState().accessToken).toBe("A");
    });
  });
});
