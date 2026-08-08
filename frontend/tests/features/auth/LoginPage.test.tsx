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
const completeMfaEnrollment = vi.fn();
const recoverMfa = vi.fn();
const startMfaEnrollment = vi.fn();
const verifyMfa = vi.fn();

vi.mock("@/features/auth/api", () => ({
  completeMfaEnrollment: (...args: unknown[]) => completeMfaEnrollment(...args),
  recoverMfa: (...args: unknown[]) => recoverMfa(...args),
  requestLoginCode: (...args: unknown[]) => requestLoginCode(...args),
  startMfaEnrollment: (...args: unknown[]) => startMfaEnrollment(...args),
  verifyMfa: (...args: unknown[]) => verifyMfa(...args),
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
    completeMfaEnrollment.mockReset();
    recoverMfa.mockReset();
    startMfaEnrollment.mockReset();
    verifyMfa.mockReset();
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
    expect(requestLoginCode).toHaveBeenCalledWith(
      { email: "owner@aurum.tj" },
      expect.any(AbortSignal),
    );
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
      expect(verifyLoginCode).toHaveBeenCalledWith(
        {
          email: "owner@aurum.tj",
          code: "123456",
          password: undefined,
        },
        expect.any(AbortSignal),
      );
      expect(navigate).toHaveBeenCalledWith({ to: "/" });
      expect(useAuthStore.getState().accessToken).toBe("A");
    });
  });

  it("completes the support login only after a valid TOTP code", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: "123456" });
    verifyLoginCode.mockResolvedValueOnce({
      status: "mfa_required",
      challenge_token: "challenge-1",
      expires_in: 300,
    });
    verifyMfa.mockResolvedValueOnce({
      access_token: "support-access",
      token_type: "bearer",
      expires_in: 900,
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "support@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const passwordInput = await screen.findByLabelText(/Пароль/i);
    fireEvent.change(passwordInput, { target: { value: "strong-password" } });
    fireEvent.submit(passwordInput.closest("form")!);

    const mfaInput = await screen.findByLabelText("Код подтверждения");
    fireEvent.change(mfaInput, { target: { value: "654321" } });
    fireEvent.submit(mfaInput.closest("form")!);

    await waitFor(() => {
      expect(verifyLoginCode).toHaveBeenCalledWith(
        {
          email: "support@aurum.tj",
          code: "123456",
          password: "strong-password",
        },
        expect.any(AbortSignal),
      );
      expect(verifyMfa).toHaveBeenCalledWith(
        {
          challenge_token: "challenge-1",
          code: "654321",
        },
        expect.any(AbortSignal),
      );
      expect(useAuthStore.getState().accessToken).toBe("support-access");
      expect(navigate).toHaveBeenCalledWith({ to: "/" });
    });
  });

  it("requires recovery-code confirmation before enabling a new support factor", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: "123456" });
    verifyLoginCode.mockResolvedValueOnce({
      status: "mfa_enrollment_required",
      challenge_token: "challenge-enroll",
      expires_in: 300,
    });
    startMfaEnrollment.mockResolvedValueOnce({
      status: "mfa_enrollment_ready",
      secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://totp/Aurum%20Pharma",
      recovery_codes: ["AAAAA-BBBBB-CCCCC-22222", "AAAAA-BBBBB-CCCCC-33333"],
      expires_in: 300,
    });
    completeMfaEnrollment.mockResolvedValueOnce({
      access_token: "enrolled-access",
      token_type: "bearer",
      expires_in: 900,
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "support@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const codeInput = await screen.findByLabelText(/Код из письма/i);
    fireEvent.submit(codeInput.closest("form")!);

    expect(await screen.findByText("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByText("AAAAA-BBBBB-CCCCC-22222")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Код из приложения"), {
      target: { value: "654321" },
    });
    fireEvent.submit(screen.getByLabelText("Код из приложения").closest("form")!);

    expect(await screen.findByText("Сохраните резервные коды")).toBeInTheDocument();
    expect(completeMfaEnrollment).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.submit(screen.getByLabelText("Код из приложения").closest("form")!);

    await waitFor(() => {
      expect(startMfaEnrollment).toHaveBeenCalledWith(
        {
          challenge_token: "challenge-enroll",
        },
        expect.any(AbortSignal),
      );
      expect(completeMfaEnrollment).toHaveBeenCalledWith(
        {
          challenge_token: "challenge-enroll",
          code: "654321",
        },
        expect.any(AbortSignal),
      );
      expect(useAuthStore.getState().accessToken).toBe("enrolled-access");
    });
  });

  it("uses a one-time recovery code and forces enrollment of a replacement factor", async () => {
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: "123456" });
    verifyLoginCode.mockResolvedValueOnce({
      status: "mfa_required",
      challenge_token: "challenge-recover",
      expires_in: 300,
    });
    recoverMfa.mockResolvedValueOnce({
      status: "mfa_enrollment_required",
      challenge_token: "challenge-recover",
      expires_in: 600,
    });
    startMfaEnrollment.mockResolvedValueOnce({
      status: "mfa_enrollment_ready",
      secret: "KRUGS4ZANFZSAYJAMNXW2L3ON5XCA5DF",
      provisioning_uri: "otpauth://totp/Aurum%20Pharma",
      recovery_codes: ["AAAAA-BBBBB-CCCCC-22222"],
      expires_in: 600,
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "support@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const codeInput = await screen.findByLabelText(/Код из письма/i);
    fireEvent.submit(codeInput.closest("form")!);
    fireEvent.click(await screen.findByRole("button", { name: /резервный код/i }));

    const recoveryInput = await screen.findByLabelText("Резервный код");
    fireEvent.change(recoveryInput, {
      target: { value: "AAAAA-BBBBB-CCCCC-22222" },
    });
    fireEvent.submit(recoveryInput.closest("form")!);

    expect(await screen.findByText("KRUGS4ZANFZSAYJAMNXW2L3ON5XCA5DF")).toBeInTheDocument();
    expect(recoverMfa).toHaveBeenCalledWith(
      {
        challenge_token: "challenge-recover",
        recovery_code: "AAAAA-BBBBB-CCCCC-22222",
      },
      expect.any(AbortSignal),
    );
    expect(startMfaEnrollment).toHaveBeenCalledWith(
      {
        challenge_token: "challenge-recover",
      },
      expect.any(AbortSignal),
    );
  });

  it("blocks account switching during verification and clears credentials afterwards", async () => {
    let rejectVerification: (reason?: unknown) => void = () => undefined;
    requestLoginCode
      .mockResolvedValueOnce({ status: "ok", dev_code: "123456" })
      .mockResolvedValueOnce({ status: "ok", dev_code: null });
    verifyLoginCode.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectVerification = reject;
        }),
    );
    renderPage();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "first@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const passwordInput = await screen.findByLabelText(/Пароль/i);
    fireEvent.change(passwordInput, { target: { value: "old-password" } });
    fireEvent.submit(passwordInput.closest("form")!);
    await waitFor(() => expect(verifyLoginCode).toHaveBeenCalledTimes(1));

    const changeEmailButton = screen.getByRole("button", { name: "Изменить email" });
    expect(changeEmailButton).toBeDisabled();

    rejectVerification(new Error("network timeout"));
    await waitFor(() => expect(changeEmailButton).toBeEnabled());
    fireEvent.click(changeEmailButton);
    expect(await screen.findByRole("button", { name: /Получить код/i })).toBeInTheDocument();
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "second@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);

    expect(await screen.findByLabelText(/Код из письма/i)).toHaveValue("");
    expect(screen.getByLabelText(/Пароль/i)).toHaveValue("");
  });

  it("keeps the recovery transition disabled while MFA verification is pending", async () => {
    let resolveMfa: (value: unknown) => void = () => undefined;
    requestLoginCode.mockResolvedValueOnce({ status: "ok", dev_code: "123456" });
    verifyLoginCode.mockResolvedValueOnce({
      status: "mfa_required",
      challenge_token: "challenge-1",
      expires_in: 300,
    });
    verifyMfa.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveMfa = resolve;
        }),
    );
    renderPage();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "support@aurum.tj" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Получить код/i }).closest("form")!);
    const codeInput = await screen.findByLabelText(/Код из письма/i);
    fireEvent.submit(codeInput.closest("form")!);

    const mfaInput = await screen.findByLabelText("Код подтверждения");
    fireEvent.change(mfaInput, { target: { value: "654321" } });
    fireEvent.submit(mfaInput.closest("form")!);
    await waitFor(() => expect(verifyMfa).toHaveBeenCalledTimes(1));

    const recoveryButton = screen.getByRole("button", { name: /резервный код/i });
    expect(recoveryButton).toBeDisabled();

    resolveMfa({
      access_token: "support-access",
      token_type: "bearer",
      expires_in: 900,
    });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/" }));
  });
});
