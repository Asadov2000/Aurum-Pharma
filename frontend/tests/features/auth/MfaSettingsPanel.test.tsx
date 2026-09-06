import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";

const api = vi.hoisted(() => ({
  fetchMfaSettings: vi.fn(),
  startAccountMfaEnrollment: vi.fn(),
  confirmAccountMfaEnrollment: vi.fn(),
  disableAccountMfa: vi.fn(),
  requestPasswordSetupCode: vi.fn(),
  setupAccountPassword: vi.fn(),
}));
vi.mock("@/features/auth/accountSecurityApi", () => api);

import { MfaSettingsPanel } from "@/features/auth/MfaSettingsPanel";

const TOKENS = { access_token: "renewed", token_type: "bearer", expires_in: 900 };
const SETUP = {
  status: "mfa_enrollment_ready",
  challenge_token: "setup-challenge",
  secret: "SAMPLESETUPKEY",
  provisioning_uri: "otpauth://totp/test",
  recovery_codes: ["reserve-one", "reserve-two"],
  expires_in: 300,
};

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MfaSettingsPanel />
      </QueryClientProvider>,
    ),
  };
}

describe("voluntary MFA settings", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useAuthStore
      .getState()
      .setTokens({ access_token: "current", token_type: "bearer", expires_in: 900 });
    api.fetchMfaSettings.mockResolvedValue({
      enabled: false,
      prompt_pending: true,
      has_password: true,
    });
    api.startAccountMfaEnrollment.mockResolvedValue(SETUP);
    api.confirmAccountMfaEnrollment.mockResolvedValue(TOKENS);
    api.disableAccountMfa.mockResolvedValue(TOKENS);
  });
  afterEach(() => act(() => useAuthStore.getState().clear()));

  it("does not activate MFA until a valid code and saved-code acknowledgement are submitted", async () => {
    const { client } = renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Включить защиту" }));
    fireEvent.change(screen.getByLabelText("Пароль аккаунта"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Продолжить настройку" }));
    expect(await screen.findByText(SETUP.secret)).toBeInTheDocument();
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ),
    ).not.toContain(SETUP.secret);
    fireEvent.change(screen.getByLabelText("Код из приложения"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и включить" }));
    expect(
      await screen.findByText("Сохраните резервные коды перед включением защиты"),
    ).toBeInTheDocument();
    expect(api.confirmAccountMfaEnrollment).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Я сохранил резервные коды в безопасном месте"));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и включить" }));
    expect(await screen.findByText("Двухфакторная защита включена.")).toBeInTheDocument();
    expect(api.confirmAccountMfaEnrollment).toHaveBeenCalledWith({
      challenge_token: "setup-challenge",
      code: "123456",
    });
    expect(useAuthStore.getState().accessToken).toBe("renewed");
    expect(screen.queryByText(SETUP.secret)).not.toBeInTheDocument();
  });

  it("cancels enrollment without enabling MFA and removes secrets from view", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Включить защиту" }));
    fireEvent.change(screen.getByLabelText("Пароль аккаунта"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Продолжить настройку" }));
    await screen.findByText(SETUP.secret);
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByText("Выключена")).toBeInTheDocument();
    expect(screen.queryByText(SETUP.secret)).not.toBeInTheDocument();
    expect(api.confirmAccountMfaEnrollment).not.toHaveBeenCalled();
  });

  it("requires only the account password to disable MFA", async () => {
    api.fetchMfaSettings.mockResolvedValue({
      enabled: true,
      prompt_pending: false,
      has_password: true,
    });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Выключить защиту" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить отключение" }));
    expect(await screen.findByText("Введите пароль")).toBeInTheDocument();
    expect(api.disableAccountMfa).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Код из приложения")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Резервный код")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Пароль аккаунта"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить отключение" }));
    expect(await screen.findByText("Двухфакторная защита выключена.")).toBeInTheDocument();
    expect(api.disableAccountMfa).toHaveBeenCalledWith({
      password: "test-password",
    });
  });

  it("cancels disabling without sending a request", async () => {
    api.fetchMfaSettings.mockResolvedValue({
      enabled: true,
      prompt_pending: false,
      has_password: true,
    });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Выключить защиту" }));
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByText("Включена")).toBeInTheDocument();
    expect(api.disableAccountMfa).not.toHaveBeenCalled();
  });

  it("keeps MFA enabled when the server rejects disabling", async () => {
    api.fetchMfaSettings.mockResolvedValue({
      enabled: true,
      prompt_pending: false,
      has_password: true,
    });
    api.disableAccountMfa.mockRejectedValue(new Error("unavailable"));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Выключить защиту" }));
    fireEvent.change(screen.getByLabelText("Пароль аккаунта"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить отключение" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Включена")).toBeInTheDocument();
    expect(useAuthStore.getState().accessToken).toBe("current");
  });

  it("lets a passwordless user create a password with an email code", async () => {
    api.fetchMfaSettings.mockResolvedValueOnce({
      enabled: false,
      prompt_pending: true,
      has_password: false,
    });
    api.requestPasswordSetupCode.mockResolvedValue({ status: "ok", dev_code: "123456" });
    api.setupAccountPassword.mockResolvedValue(undefined);
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "Получить код для создания пароля" }),
    );
    await screen.findByLabelText("Код из письма");
    fireEvent.change(screen.getByLabelText("Новый пароль"), {
      target: { value: "new-long-password" },
    });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), {
      target: { value: "new-long-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить пароль" }));
    await waitFor(() =>
      expect(api.setupAccountPassword).toHaveBeenCalledWith({
        code: "123456",
        new_password: "new-long-password",
      }),
    );
    expect(await screen.findByRole("button", { name: "Включить защиту" })).toBeInTheDocument();
  });
});
