import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listPlatformAccessGrants = vi.fn();
const approvePlatformAccessGrant = vi.fn();
const revokePlatformAccessGrant = vi.fn();
const authState = vi.hoisted(() => ({
  user: {
    id: "developer-1",
    is_developer: true,
    is_administrator: false,
    active_tenant_id: null,
    home_tenant_id: null,
    support_access: null,
    platform_capabilities: ["platform.access.view", "platform.access.manage"] as string[],
  },
}));

vi.mock("@/features/platformAccess/api", () => ({
  listPlatformAccessGrants: (...args: unknown[]) => listPlatformAccessGrants(...args),
  approvePlatformAccessGrant: (...args: unknown[]) => approvePlatformAccessGrant(...args),
  revokePlatformAccessGrant: (...args: unknown[]) => revokePlatformAccessGrant(...args),
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => authState,
}));

import { PlatformAccessPage } from "@/features/platformAccess/PlatformAccessPage";
import { type PlatformAccessGrant } from "@/features/platformAccess/types";

const GRANT: PlatformAccessGrant = {
  id: "grant-1",
  user_id: "administrator-1",
  user_email: "admin@aurum.tj",
  user_full_name: "Demo Administrator",
  access_kind: "administrator",
  capabilities: ["platform.tenants.view", "platform.support.use"],
  status: "pending",
  requested_by: "developer-2",
  request_reason_code: "platform_staff_onboarding",
  request_reason: "Подключение администратора для поддержки пилотной аптеки",
  requested_at: "2026-08-10T12:00:00Z",
  requires_approval: true,
  approval_expires_at: "2026-08-10T12:15:00Z",
  approved_by: null,
  approved_at: null,
  approval_reason_code: null,
  approval_reason: null,
  revoked_by: null,
  revoked_at: null,
  revoke_reason_code: null,
  revoke_reason: null,
  version: 1,
  created_at: "2026-08-10T12:00:00Z",
  updated_at: "2026-08-10T12:00:00Z",
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformAccessPage />
    </QueryClientProvider>,
  );
}

async function openAction(label: "Подтвердить" | "Отозвать") {
  fireEvent.click(await screen.findByRole("button", { name: "Действия с доступом" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: label }));
}

describe("PlatformAccessPage", () => {
  beforeEach(() => {
    listPlatformAccessGrants.mockReset();
    approvePlatformAccessGrant.mockReset();
    revokePlatformAccessGrant.mockReset();
    authState.user.platform_capabilities = ["platform.access.view", "platform.access.manage"];
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the account identity, full status and capability summary", async () => {
    listPlatformAccessGrants.mockResolvedValue([GRANT]);

    renderPage();

    expect(await screen.findByText("Demo Administrator")).toBeInTheDocument();
    expect(screen.getByText("admin@aurum.tj")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("Ожидает подтверждения"))
      .toBeInTheDocument();
    expect(screen.getByText("Просмотр аптек")).toBeInTheDocument();
    expect(screen.getByText("Защищённая поддержка")).toBeInTheDocument();
  });

  it("keeps all mutation controls out of the DOM for view-only access", async () => {
    authState.user.platform_capabilities = ["platform.access.view"];
    listPlatformAccessGrants.mockResolvedValue([GRANT]);

    renderPage();

    expect(await screen.findByText("Demo Administrator")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Действия с доступом" })).not.toBeInTheDocument();
    expect(screen.queryByText("Действия")).not.toBeInTheDocument();
  });

  it("does not allow approving a request made by the current developer", async () => {
    listPlatformAccessGrants.mockResolvedValue([{ ...GRANT, requested_by: "developer-1" }]);

    renderPage();

    expect(await screen.findByText("Нужен другой разработчик")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Действия с доступом" }));
    expect(screen.queryByRole("menuitem", { name: "Подтвердить" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Отозвать" })).toBeInTheDocument();
  });

  it("submits approval with the current version and a reviewed reason", async () => {
    listPlatformAccessGrants.mockResolvedValue([GRANT]);
    approvePlatformAccessGrant.mockResolvedValue({ ...GRANT, status: "active", version: 2 });

    renderPage();
    await openAction("Подтвердить");

    expect(screen.getByText(GRANT.request_reason)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Комментарий"), {
      target: { value: "Проверил обязанности и подтверждаю доступ" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() => {
      expect(approvePlatformAccessGrant).toHaveBeenCalledWith("grant-1", {
        version: 1,
        reason_code: "access_review",
        reason: "Проверил обязанности и подтверждаю доступ",
      });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Доступ подтверждён");
  });

  it("renders a stable empty state and supports local account search", async () => {
    listPlatformAccessGrants.mockResolvedValue([GRANT]);
    renderPage();
    await screen.findByText("Demo Administrator");

    fireEvent.change(screen.getByLabelText("Аккаунт"), {
      target: { value: "missing@aurum.tj" },
    });

    expect(await screen.findByText("Доступы не найдены")).toBeInTheDocument();
    expect(listPlatformAccessGrants).toHaveBeenCalledTimes(1);
  });
});
