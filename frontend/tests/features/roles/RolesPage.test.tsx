import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listRoles = vi.fn();
const listPermissions = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listRoles: (...args: unknown[]) => listRoles(...args),
  listPermissions: (...args: unknown[]) => listPermissions(...args),
  listTemplates: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  suspendUser: vi.fn(),
  offboardUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { RolesPage } from "@/features/roles/RolesPage";

const MANAGED_ROLE = {
  id: "role-managed",
  tenant_id: "tenant-1",
  name: "Старший кассир",
  description: null,
  is_system: false,
  is_protected: false,
  protected_kind: null,
  is_active: true,
  version: 1,
  permissions: ["pos.sell"],
};

const SYSTEM_ROLE = {
  ...MANAGED_ROLE,
  id: "role-system",
  tenant_id: null,
  name: "Aurum Administrator",
  is_system: true,
  is_protected: true,
  protected_kind: "administrator",
};

const PROTECTED_ROLE = {
  ...MANAGED_ROLE,
  id: "role-protected",
  name: "Владелец",
  is_protected: true,
};

const FOREIGN_ROLE = {
  ...MANAGED_ROLE,
  id: "role-foreign",
  tenant_id: "tenant-2",
  name: "Чужая роль",
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RolesPage />
    </QueryClientProvider>,
  );
}

describe("RolesPage", () => {
  beforeEach(() => {
    mockUser = {
      home_tenant_id: "tenant-1",
      permissions: ["roles.update"],
    };
    listRoles.mockReset();
    listPermissions.mockReset();
    listRoles.mockResolvedValue([MANAGED_ROLE, SYSTEM_ROLE, PROTECTED_ROLE, FOREIGN_ROLE]);
    listPermissions.mockResolvedValue([
      {
        code: "pos.sell",
        group_code: "pos",
        name: "Продажа",
        description: null,
        is_dangerous: false,
        is_active: true,
        scope_type: "TENANT_ALL",
        target_role_type: "tenant",
        risk_level: "normal",
        requires_step_up: false,
        requires_confirmation: false,
      },
    ]);
  });

  afterEach(() => vi.clearAllMocks());

  it("shows only roles owned by the current tenant and never exposes protected roles", async () => {
    renderPage();

    expect(await screen.findByText("Старший кассир")).toBeInTheDocument();
    expect(screen.queryByText("Aurum Administrator")).not.toBeInTheDocument();
    expect(screen.queryByText("Владелец")).not.toBeInTheDocument();
    expect(screen.queryByText("Чужая роль")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Изменить" })).toHaveLength(1);
  });

  it("gates create and update independently", async () => {
    mockUser = {
      home_tenant_id: "tenant-1",
      permissions: ["roles.create"],
    };
    renderPage();

    expect(await screen.findByText("Старший кассир")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Создать роль" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
  });

  it("renders a request error without an empty-state fallback", async () => {
    listRoles.mockRejectedValue(new Error("roles failed"));
    renderPage();

    expect(await screen.findByText(/Не удалось загрузить список/i)).toBeInTheDocument();
    expect(screen.queryByText(/Управляемых ролей пока нет/i)).not.toBeInTheDocument();
  });
});
