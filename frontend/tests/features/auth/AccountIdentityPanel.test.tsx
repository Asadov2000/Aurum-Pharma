import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = {
  phone: "+992901111111",
  phone_verified: true,
  has_password: true,
  password_prompt_pending: false,
};
const requestCode = vi.fn();
const verifyCode = vi.fn();
const requestReauth = vi.fn();
let mfaEnabled = false;
vi.mock("@/features/auth/identityQueries", () => ({
  accountIdentityQueryKey: ["auth", "identity"],
  useAccountIdentityQuery: () => ({ data: identity, isPending: false, isError: false }),
  useLoginMethodsQuery: () => ({ data: { phone_login_enabled: true } }),
}));
vi.mock("@/features/auth/accountSecurityQueries", () => ({
  mfaSettingsQueryKey: ["auth", "mfa-settings"],
  useMfaSettingsQuery: () => ({ data: { enabled: mfaEnabled } }),
}));
vi.mock("@/features/auth/identityApi", () => ({
  requestPhoneChangeCode: (...args: unknown[]) => requestCode(...args),
  verifyPhoneChangeCode: (...args: unknown[]) => verifyCode(...args),
  requestPhoneReauthenticationCode: (...args: unknown[]) => requestReauth(...args),
}));
import { AccountIdentityPanel } from "@/features/auth/AccountIdentityPanel";

function renderPanel() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AccountIdentityPanel />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Изменить номер" }));
  fireEvent.change(screen.getByLabelText("Новый номер телефона"), {
    target: { value: "90 222 22 22" },
  });
}

describe("phone binding settings", () => {
  beforeEach(() => {
    identity.has_password = true;
    mfaEnabled = false;
    requestCode.mockReset().mockResolvedValue({ status: "ok", dev_code: "123456" });
    verifyCode.mockReset().mockResolvedValue(undefined);
    requestReauth.mockReset().mockResolvedValue({ status: "ok", dev_code: "654321" });
  });

  it("requires current password and sends SMS only to the normalized new number", async () => {
    renderPanel();
    fireEvent.submit(
      screen.getByRole("button", { name: "Отправить SMS на новый номер" }).closest("form")!,
    );
    expect(await screen.findByText("Введите текущий пароль")).toBeInTheDocument();
    expect(requestCode).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Текущий пароль"), {
      target: { value: "current-password" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: "Отправить SMS на новый номер" }).closest("form")!,
    );
    const code = await screen.findByLabelText("Код на новый номер");
    expect(requestCode).toHaveBeenCalledWith({
      phone: "+992902222222",
      password: "current-password",
      reauth_code: undefined,
      mfa_code: undefined,
    });
    expect(verifyCode).not.toHaveBeenCalled();
    fireEvent.submit(code.closest("form")!);
    expect(
      await screen.findByText("Телефон подтверждён. Теперь можно входить по SMS."),
    ).toBeInTheDocument();
    expect(verifyCode).toHaveBeenCalledWith({ phone: "+992902222222", code: "123456" });
  });

  it("requires enabled MFA in addition to the password", async () => {
    mfaEnabled = true;
    renderPanel();
    fireEvent.change(screen.getByLabelText("Текущий пароль"), {
      target: { value: "current-password" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: "Отправить SMS на новый номер" }).closest("form")!,
    );
    expect(await screen.findByText("Введите 6 цифр из приложения")).toBeInTheDocument();
    expect(requestCode).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Код из приложения"), { target: { value: "456789" } });
    fireEvent.submit(
      screen.getByRole("button", { name: "Отправить SMS на новый номер" }).closest("form")!,
    );
    await waitFor(() =>
      expect(requestCode).toHaveBeenCalledWith(expect.objectContaining({ mfa_code: "456789" })),
    );
  });

  it("supports existing-contact proof when the account has no password", async () => {
    identity.has_password = false;
    renderPanel();
    expect(screen.queryByLabelText("Текущий пароль")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить личность через почту" }));
    await waitFor(() => expect(screen.getByLabelText("Код из письма")).toHaveValue("654321"));
    fireEvent.submit(
      screen.getByRole("button", { name: "Отправить SMS на новый номер" }).closest("form")!,
    );
    await waitFor(() =>
      expect(requestCode).toHaveBeenCalledWith({
        phone: "+992902222222",
        password: undefined,
        reauth_code: "654321",
        mfa_code: undefined,
      }),
    );
  });
});
