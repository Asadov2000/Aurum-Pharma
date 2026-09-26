import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TenantAccessBanner } from "@/features/tenantAccess/TenantAccessBanner";

const getPolicy = vi.fn();
const context = { edge: false, online: true, authenticated: true };
const user = {
  id: "user-1",
  active_tenant_id: "tenant-1",
  home_tenant_id: "tenant-1",
  is_developer: false,
  is_administrator: false,
};
vi.mock("@/features/tenantAccess/api", () => ({
  getTenantAccessPolicy: (...args: unknown[]) => getPolicy(...args),
}));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user, isAuthenticated: context.authenticated }),
}));
vi.mock("@/features/edgeReadModel/dataPlane", () => ({
  resolveDataPlane: () => ({ mode: context.edge ? "edge-readonly" : "cloud" }),
}));
vi.mock("@/lib/connectivityContext", () => ({
  useConnectivity: () => ({ canUseServer: context.online }),
}));
const policy = {
  tenant_id: "tenant-1",
  mode: "full",
  blocked_modules: [],
  expires_at: null,
  version: 1,
  max_branches: null,
  max_users: null,
  max_registers: null,
  updated_at: "2026-01-01T00:00:00Z",
};
function renderBanner() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <TenantAccessBanner />
    </QueryClientProvider>,
  );
}
describe("TenantAccessBanner", () => {
  beforeEach(() => {
    getPolicy.mockReset().mockResolvedValue(policy);
    context.edge = false;
    context.online = true;
    context.authenticated = true;
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });
  it("keeps full access quiet and fetches the sanitized tenant projection", async () => {
    renderBanner();
    await waitFor(() => expect(getPolicy).toHaveBeenCalledTimes(1));
    expect(getPolicy).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
  it("explains readonly access without exposing internal reasons", async () => {
    getPolicy.mockResolvedValue({
      ...policy,
      mode: "readonly",
      reason: "internal-administrator-note",
    });
    renderBanner();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Рабочие операции доступны только для чтения.",
    );
    expect(screen.getByRole("status")).toHaveTextContent("Чтение доступно по вашим правам.");
    expect(document.body.textContent).not.toContain("internal-administrator-note");
  });
  it("explains that inventory restrictions also affect stock-changing operations", async () => {
    getPolicy.mockResolvedValue({ ...policy, blocked_modules: ["inventory"] });
    renderBanner();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Изменения ограничены в разделах: склад.",
    );
    expect(screen.getByRole("status")).toHaveTextContent("продажи, приёмку и возвраты");
  });
  it("hides the banner at expiry without waiting for the next server poll", async () => {
    vi.useFakeTimers();
    getPolicy.mockResolvedValue({
      ...policy,
      mode: "readonly",
      expires_at: new Date(Date.now() + 1000).toISOString(),
    });
    renderBanner();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByRole("status")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
  it.each(["edge", "offline", "unauthenticated"])(
    "does not request cloud policy in %s context",
    async (mode) => {
      context.edge = mode === "edge";
      context.online = mode !== "offline";
      context.authenticated = mode !== "unauthenticated";
      renderBanner();
      await act(async () => {
        await Promise.resolve();
      });
      expect(getPolicy).not.toHaveBeenCalled();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    },
  );
});
