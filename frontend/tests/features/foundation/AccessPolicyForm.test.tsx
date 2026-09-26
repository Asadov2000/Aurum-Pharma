import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccessPolicyForm } from "@/features/foundation/AccessPolicyForm";

const apiGet = vi.fn();
const apiPatch = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
  },
}));
const policy = {
  mode: "full",
  blocked_modules: [],
  max_branches: null,
  max_users: 10,
  max_registers: null,
  expires_at: null,
  reason: "",
  version: 7,
};
const close = vi.fn();
function renderForm(canManage = true) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AccessPolicyForm tenantId="tenant-1" canManage={canManage} onClose={close} />
    </QueryClientProvider>,
  );
}
describe("AccessPolicyForm", () => {
  beforeEach(() => {
    apiGet.mockReset().mockResolvedValue({ data: policy });
    apiPatch.mockReset().mockResolvedValue({ data: { ...policy, version: 8 } });
    close.mockReset();
  });
  afterEach(cleanup);
  it("previews restrictions and sends the version with a reason", async () => {
    renderForm();
    fireEvent.change(await screen.findByLabelText("Режим доступа"), {
      target: { value: "readonly" },
    });
    fireEvent.change(screen.getByLabelText("Лимит сотрудников"), { target: { value: "5" } });
    fireEvent.click(screen.getByLabelText("Склад"));
    fireEvent.change(screen.getByLabelText("Причина изменения"), {
      target: { value: "Запрос владельца" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить последствия" }));
    expect(await screen.findByText("Последствия изменения")).toBeInTheDocument();
    expect(screen.getByText(/Запрет изменений склада также остановит продажи/)).toBeInTheDocument();
    expect(apiPatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить изменение" }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith(
        "/admin/tenants/tenant-1/access-policy",
        expect.objectContaining({
          mode: "readonly",
          max_users: 5,
          reason: "Запрос владельца",
          expected_version: 7,
        }),
      ),
    );
    await waitFor(() => expect(close).toHaveBeenCalled());
  });
  it("requires a reason and does not send invalid settings", async () => {
    renderForm();
    await screen.findByLabelText("Режим доступа");
    fireEvent.click(screen.getByRole("button", { name: "Проверить последствия" }));
    expect(await screen.findByText("Укажите причину: минимум 3 символа")).toBeInTheDocument();
    expect(apiPatch).not.toHaveBeenCalled();
  });
  it("rejects a zero quota before previewing", async () => {
    renderForm();
    fireEvent.change(await screen.findByLabelText("Лимит сотрудников"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Причина изменения"), {
      target: { value: "Проверка лимита" },
    });
    fireEvent.submit(screen.getByLabelText("Лимит сотрудников").closest("form")!);
    expect(
      await screen.findByText("Введите целое число от 1 до 2147483647 или оставьте поле пустым"),
    ).toBeInTheDocument();
    expect(apiPatch).not.toHaveBeenCalled();
  });
  it("allows a viewer to inspect but not change the policy", async () => {
    renderForm(false);
    expect(await screen.findByLabelText("Режим доступа")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Проверить последствия" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Причина изменения")).not.toBeInTheDocument();
  });
  it("blocks stale updates and requires reloading after a conflict", async () => {
    const config = { headers: new AxiosHeaders() };
    apiPatch.mockRejectedValue(
      new AxiosError("conflict", "ERR_BAD_REQUEST", config, undefined, {
        status: 409,
        statusText: "Conflict",
        data: {},
        headers: {},
        config,
      }),
    );
    renderForm();
    fireEvent.change(await screen.findByLabelText("Причина изменения"), {
      target: { value: "Проверка доступа" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить последствия" }));
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить изменение" }));
    expect(await screen.findByText(/уже изменены другим администратором/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить изменение" })).toBeDisabled();
    expect(close).not.toHaveBeenCalled();
  });
});
