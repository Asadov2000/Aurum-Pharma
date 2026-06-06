import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listUsers = vi.fn();
const listRoles = vi.fn();
const inviteUser = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listUsers: (...a: unknown[]) => listUsers(...a),
  listRoles: (...a: unknown[]) => listRoles(...a),
  listPermissions: vi.fn().mockResolvedValue([]),
  inviteUser: (...a: unknown[]) => inviteUser(...a),
  updateUser: vi.fn(),
  blockUser: vi.fn(),
  archiveUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: vi.fn().mockResolvedValue([]),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  updateTenant: vi.fn(),
  getTenantSettings: vi.fn(),
  updateTenantSettings: vi.fn(),
  createBranch: vi.fn(),
  updateBranch: vi.fn(),
  deleteBranch: vi.fn(),
  listRegisters: vi.fn(),
  createRegister: vi.fn(),
  updateRegister: vi.fn(),
  deleteRegister: vi.fn(),
}));

// The page now gates itself behind users.view; render as an owner who holds it
// so the user/role queries stay enabled (the access gate is covered separately
// in RouteAccess.test.tsx).
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { home_tenant_id: "t-1", permissions: ["users.view"] } }),
}));

import { UsersPage } from "@/features/roles/UsersPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UsersPage />
    </QueryClientProvider>,
  );
}

const ROLE = {
  id: "33333333-3333-3333-3333-333333333333",
  tenant_id: null,
  name: "Кассир",
  description: null,
  level: 4,
  is_system: true,
  is_active: true,
  permissions: ["pos.sell"],
};

const USER_ACTIVE = {
  id: "u-1",
  email: "u1@aurum.tj",
  full_name: "User One",
  phone: null,
  status: "active" as const,
  last_login_at: null,
  assignments: [
    {
      id: "a-1",
      user_id: "u-1",
      tenant_id: "t-1",
      branch_id: null,
      role_id: ROLE.id,
      password_required: false,
      is_active: true,
    },
  ],
};

describe("UsersPage", () => {
  beforeEach(() => {
    listUsers.mockReset();
    listRoles.mockReset();
    inviteUser.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders an empty state when there are no users", async () => {
    listUsers.mockResolvedValueOnce([]);
    listRoles.mockResolvedValueOnce([ROLE]);
    renderPage();
    expect(await screen.findByText(/Пока нет пользователей/i)).toBeInTheDocument();
  });

  it("renders the role name from the role registry, not the raw id", async () => {
    listUsers.mockResolvedValueOnce([USER_ACTIVE]);
    listRoles.mockResolvedValueOnce([ROLE]);
    renderPage();
    expect(await screen.findByText("User One")).toBeInTheDocument();
    expect(await screen.findByText("Кассир")).toBeInTheDocument();
    expect(screen.queryByText(ROLE.id)).not.toBeInTheDocument();
  });

  it("rejects the invite form when required fields are empty", async () => {
    listUsers.mockResolvedValueOnce([]);
    listRoles.mockResolvedValueOnce([ROLE]);
    renderPage();
    await screen.findByText(/Пока нет пользователей/i);
    fireEvent.click(screen.getByRole("button", { name: /\+ Пригласить/i }));
    const submit = await screen.findByRole("button", { name: /^Пригласить$/i });
    fireEvent.click(submit);
    expect(await screen.findByText(/Некорректный email/i)).toBeInTheDocument();
    expect(inviteUser).not.toHaveBeenCalled();
  });

  it("invites a user with the selected role", async () => {
    listUsers.mockResolvedValue([]);
    listRoles.mockResolvedValue([ROLE]);
    inviteUser.mockResolvedValueOnce({ id: "a-new" });
    renderPage();
    await screen.findByText(/Пока нет пользователей/i);
    fireEvent.click(screen.getByRole("button", { name: /\+ Пригласить/i }));
    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "new@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText("Имя"), { target: { value: "Новый" } });
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: ROLE.id } });
    fireEvent.click(screen.getByRole("button", { name: /^Пригласить$/i }));
    await waitFor(() => {
      expect(inviteUser).toHaveBeenCalledTimes(1);
    });
    expect(inviteUser).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "new@aurum.tj",
        full_name: "Новый",
        role_id: ROLE.id,
        branch_id: null,
        password_required: false,
      }),
    );
  });
});
