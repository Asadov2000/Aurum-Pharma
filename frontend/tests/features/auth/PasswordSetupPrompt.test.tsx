import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = { has_password: false, password_prompt_pending: true, phone_verified: true };
const dismiss = vi.fn();
vi.mock("@/features/auth/identityQueries", () => ({
  accountIdentityQueryKey: ["auth", "identity"],
  useAccountIdentityQuery: () => ({ data: identity }),
  useLoginMethodsQuery: () => ({ data: { phone_login_enabled: true } }),
}));
vi.mock("@/features/auth/identityApi", () => ({
  dismissPasswordPrompt: (...args: unknown[]) => dismiss(...args),
}));
vi.mock("@/features/auth/PasswordSetupForm", () => ({
  PasswordSetupForm: ({ phoneAvailable }: { phoneAvailable: boolean }) => (
    <p>
      {phoneAvailable
        ? "Создание пароля через подтверждённый телефон"
        : "Создание пароля через почту"}
    </p>
  ),
}));
import { PasswordSetupPrompt } from "@/features/auth/PasswordSetupPrompt";

function renderPrompt() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PasswordSetupPrompt />
    </QueryClientProvider>,
  );
}

describe("first login password suggestion", () => {
  beforeEach(() => {
    identity.has_password = false;
    identity.password_prompt_pending = true;
    dismiss.mockReset().mockResolvedValue(undefined);
  });

  it("offers real password setup and supports a verified phone", () => {
    renderPrompt();
    fireEvent.click(screen.getByRole("button", { name: "Создать пароль" }));
    expect(screen.getByText("Создание пароля через подтверждённый телефон")).toBeInTheDocument();
  });

  it("persists postponement through the backend", async () => {
    renderPrompt();
    fireEvent.click(screen.getByRole("button", { name: "Позже" }));
    await waitFor(() => expect(dismiss).toHaveBeenCalledTimes(1));
  });

  it.each(["password", "dismissed"] as const)(
    "does not offer setup when %s is already saved",
    (reason) => {
      identity.has_password = reason === "password";
      identity.password_prompt_pending = reason !== "dismissed";
      renderPrompt();
      expect(
        screen.queryByRole("region", { name: "Предложение создать пароль" }),
      ).not.toBeInTheDocument();
    },
  );
});
