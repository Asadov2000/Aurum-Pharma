import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const requestCode = vi.fn();
const resetPassword = vi.fn();
const back = vi.fn();
vi.mock("@/features/auth/identityApi", () => ({
  requestPasswordResetCode: (...args: unknown[]) => requestCode(...args),
  resetAccountPassword: (...args: unknown[]) => resetPassword(...args),
  fetchLoginMethods: async () => ({ phone_login_enabled: true }),
}));
import { PasswordResetForm } from "@/features/auth/PasswordResetForm";

function renderForm() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PasswordResetForm initialChannel="email" onBack={back} />
    </QueryClientProvider>,
  );
}

async function requestRecovery() {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "account@example.test" } });
  fireEvent.submit(
    screen.getByRole("button", { name: "Получить код восстановления" }).closest("form")!,
  );
  await screen.findByLabelText("Код восстановления");
}

describe("password recovery", () => {
  beforeEach(() => {
    requestCode.mockReset().mockResolvedValue({ status: "ok", dev_code: "123456" });
    resetPassword.mockReset().mockResolvedValue(undefined);
    back.mockReset();
  });

  it("uses a generic delivery notice and requires matching passwords before any reset", async () => {
    renderForm();
    await requestRecovery();
    expect(screen.getByText(/Если аккаунт с такими данными существует/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Новый пароль"), {
      target: { value: "new-password-long" },
    });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), {
      target: { value: "different-password" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "Сохранить пароль" }).closest("form")!);
    expect(await screen.findByText("Пароли не совпадают")).toBeInTheDocument();
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("sends the chosen factor and finishes without silently logging in", async () => {
    renderForm();
    await requestRecovery();
    fireEvent.change(screen.getByLabelText("Новый пароль"), {
      target: { value: "new-password-long" },
    });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), {
      target: { value: "new-password-long" },
    });
    fireEvent.click(screen.getByLabelText("У меня включена двухфакторная защита"));
    fireEvent.change(screen.getByLabelText("Код из приложения"), { target: { value: "654321" } });
    fireEvent.submit(screen.getByRole("button", { name: "Сохранить пароль" }).closest("form")!);
    expect(await screen.findByRole("heading", { name: "Пароль обновлён" })).toBeInTheDocument();
    expect(resetPassword).toHaveBeenCalledWith(
      {
        channel: "email",
        contact: "account@example.test",
        code: "123456",
        new_password: "new-password-long",
        mfa_code: "654321",
        recovery_code: undefined,
      },
      expect.any(AbortSignal),
    );
    expect(back).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Вернуться ко входу" }));
    expect(back).toHaveBeenCalledTimes(1);
  });

  it("reveals the required second factor after the server validates the contact code", async () => {
    resetPassword.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 401, data: { error: { details: { reason: "mfa_required" } } } },
    });
    renderForm();
    await requestRecovery();
    fireEvent.change(screen.getByLabelText("Новый пароль"), {
      target: { value: "new-password-long" },
    });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), {
      target: { value: "new-password-long" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "Сохранить пароль" }).closest("form")!);
    await waitFor(() =>
      expect(screen.getByLabelText("У меня включена двухфакторная защита")).toBeChecked(),
    );
    expect(screen.getByLabelText("Код восстановления")).toHaveValue("123456");
    expect(screen.queryByRole("heading", { name: "Пароль обновлён" })).not.toBeInTheDocument();
  });
});
