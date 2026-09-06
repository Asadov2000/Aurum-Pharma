import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError, type AxiosResponse } from "axios";

const confirmAccountPassword = vi.fn();
const settingsState = vi.hoisted(() => ({ data: { has_password: true } }));
vi.mock("@/features/auth/accountSecurityQueries", () => ({
  useMfaSettingsQuery: () => settingsState,
}));

vi.mock("@/features/auth/accountSecurityApi", () => ({
  confirmAccountPassword: (...args: unknown[]) => confirmAccountPassword(...args),
}));

import { MfaStepUpDialog } from "@/features/auth/MfaStepUpDialog";
import { cancelMfaStepUp, requestMfaStepUp } from "@/features/auth/stepUpCoordinator";
import { useAuthStore } from "@/stores/auth";

describe("MfaStepUpDialog", () => {
  beforeEach(() => {
    cancelMfaStepUp();
    confirmAccountPassword.mockReset();
    settingsState.data = { has_password: true };
    useAuthStore.getState().clear();
  });

  it("validates an empty password without calling the API", async () => {
    render(<MfaStepUpDialog />);
    act(() => {
      void requestMfaStepUp();
    });

    fireEvent.change(await screen.findByLabelText("Пароль аккаунта"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(await screen.findByText("Введите пароль")).toBeInTheDocument();
    expect(confirmAccountPassword).not.toHaveBeenCalled();
  });

  it("stores the renewed token and resolves the blocked request", async () => {
    confirmAccountPassword.mockResolvedValueOnce({
      access_token: "renewed-access",
      token_type: "bearer",
      expires_in: 900,
    });
    render(<MfaStepUpDialog />);
    let pending!: Promise<string | null>;
    act(() => {
      pending = requestMfaStepUp();
    });

    fireEvent.change(await screen.findByLabelText("Пароль аккаунта"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() => {
      expect(confirmAccountPassword).toHaveBeenCalledWith("123456");
      expect(useAuthStore.getState().accessToken).toBe("renewed-access");
    });
    await expect(pending).resolves.toBe("renewed-access");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels the blocked request without logging the user out", async () => {
    useAuthStore.getState().setTokens({
      access_token: "current-access",
      token_type: "bearer",
      expires_in: 900,
    });
    render(<MfaStepUpDialog />);
    let pending!: Promise<string | null>;
    act(() => {
      pending = requestMfaStepUp();
    });

    fireEvent.click(await screen.findByRole("button", { name: "Отмена" }));

    await expect(pending).resolves.toBeNull();
    expect(useAuthStore.getState().accessToken).toBe("current-access");
  });

  it("offers password setup when no password exists without completing the blocked action", async () => {
    confirmAccountPassword.mockRejectedValueOnce(
      new AxiosError("Setup needed", undefined, undefined, undefined, {
        status: 403,
        data: { error: { details: { reason: "password_setup_required" } } },
      } as AxiosResponse),
    );
    render(<MfaStepUpDialog />);
    act(() => {
      void requestMfaStepUp();
    });
    fireEvent.change(await screen.findByLabelText("Пароль аккаунта"), {
      target: { value: "unknown-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(
      await screen.findByRole("link", { name: "Создать пароль в настройках безопасности" }),
    ).toHaveAttribute("href", "/settings?section=security");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("shows password setup immediately for a passwordless account", async () => {
    settingsState.data = { has_password: false };
    render(<MfaStepUpDialog />);
    act(() => {
      void requestMfaStepUp();
    });
    expect(
      await screen.findByRole("link", { name: "Создать пароль в настройках безопасности" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Пароль аккаунта")).not.toBeInTheDocument();
    expect(confirmAccountPassword).not.toHaveBeenCalled();
  });
});
