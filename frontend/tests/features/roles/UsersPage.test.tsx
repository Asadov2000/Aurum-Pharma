import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listUsers = vi.fn();
const listRoles = vi.fn();
const updateUser = vi.fn();
const suspendUser = vi.fn();
const offboardUser = vi.fn();
const revokeUserSessions = vi.fn();
const createAssignment = vi.fn();
const listBranches = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listUsers: (...args: unknown[]) => listUsers(...args),
  listRoles: (...args: unknown[]) => listRoles(...args),
  listPermissions: vi.fn().mockResolvedValue([]),
  listTemplates: vi.fn().mockResolvedValue([]),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  updateUser: (...args: unknown[]) => updateUser(...args),
  suspendUser: (...args: unknown[]) => suspendUser(...args),
  offboardUser: (...args: unknown[]) => offboardUser(...args),
  revokeUserSessions: (...args: unknown[]) => revokeUserSessions(...args),
  createAssignment: (...args: unknown[]) => createAssignment(...args),
  revokeAssignment: vi.fn(),
}));

vi.mock("@/features/foundation/api", () => ({
  listBranches: (...args: unknown[]) => listBranches(...args),
  listTenants: vi.fn(),
  createTenant: vi.fn(),
  createTenantOwner: vi.fn(),
  createTenantMember: vi.fn(),
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

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { UsersPage } from "@/features/roles/UsersPage";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <UsersPage />
    </QueryClientProvider>,
  );
}

async function openUserActions(fullName = "Иван Сотрудник") {
  const trigger = await screen.findByRole("button", { name: `Действия для ${fullName}` });
  fireEvent.click(trigger);
  return trigger;
}

const MANAGED_ROLE = {
  id: "role-managed",
  tenant_id: "tenant-1",
  name: "Кассир",
  description: null,
  is_system: false,
  is_protected: false,
  protected_kind: null,
  is_active: true,
  version: 1,
  permissions: ["pos.sell"],
  has_hidden_permissions: false,
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

const USER_ACTIVE = {
  id: "member-1",
  membership_id: "membership-1",
  is_tenant_owner: false,
  email: "user@aurum.tj",
  full_name: "Иван Сотрудник",
  phone: null,
  status: "active" as const,
  last_login_at: null,
  assignments: [
    {
      id: "assignment-1",
      user_id: "member-1",
      tenant_id: "tenant-1",
      membership_id: "membership-1",
      branch_id: null,
      role_id: MANAGED_ROLE.id,
      role_name: MANAGED_ROLE.name,
      password_required: false,
      is_active: true,
    },
  ],
};

const usersResponse = (items: unknown[]) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 50,
});

describe("UsersPage", () => {
  beforeEach(() => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view"],
    };
    listUsers.mockReset();
    listRoles.mockReset();
    updateUser.mockReset();
    suspendUser.mockReset();
    offboardUser.mockReset();
    revokeUserSessions.mockReset();
    createAssignment.mockReset();
    listBranches.mockReset();
    listRoles.mockResolvedValue([MANAGED_ROLE, SYSTEM_ROLE]);
    listBranches.mockResolvedValue([]);
    updateUser.mockResolvedValue({});
    suspendUser.mockResolvedValue(undefined);
    offboardUser.mockResolvedValue(undefined);
    revokeUserSessions.mockResolvedValue({ status: "ok", revoked_count: 1 });
    createAssignment.mockResolvedValue({ id: "assignment-new" });
  });

  afterEach(() => vi.clearAllMocks());

  it("renders attached-membership empty state without invite controls", async () => {
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    expect(await screen.findByText(/К аптеке пока не прикреплены сотрудники/i)).toBeInTheDocument();
    expect(screen.queryByText(/Пригласить/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Создать аккаунт/i)).not.toBeInTheDocument();
  });

  it("renders server-provided assignment names without opening the role builder", async () => {
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    expect(await screen.findByText("Иван Сотрудник")).toBeInTheDocument();
    expect(await screen.findByText("Кассир")).toBeInTheDocument();
    expect(screen.queryByText(MANAGED_ROLE.id)).not.toBeInTheDocument();
    expect(listRoles).not.toHaveBeenCalled();
  });

  it("does not derive update, suspend, offboard or assignment actions from users.view", async () => {
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    expect(await screen.findByText("Иван Сотрудник")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Профиль" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Роли" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Приостановить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Уволить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Завершить сеансы" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Действия для/ })).not.toBeInTheDocument();
  });

  it("gates profile and assignment actions independently", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    expect(await screen.findByRole("menuitem", { name: "Профиль" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Роли" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Приостановить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Уволить" })).not.toBeInTheDocument();
  });

  it("updates a profile only through users.update", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Профиль" }));
    fireEvent.change(await screen.findByLabelText("ФИО"), {
      target: { value: "Иван Обновлённый" },
    });
    fireEvent.change(screen.getByLabelText("Телефон"), {
      target: { value: "+992900001122" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    expect(updateUser).toHaveBeenCalledWith("member-1", {
      full_name: "Иван Обновлённый",
      phone: "+992900001122",
    });
  });

  it("activates a pending membership through users.update", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update"],
    };
    listUsers.mockResolvedValue(
      usersResponse([{ ...USER_ACTIVE, status: "pending", assignments: [] }]),
    );
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Активировать" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    expect(updateUser).toHaveBeenCalledWith("member-1", { status: "active" });
  });

  it("assigns only an active, manageable tenant role", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));
    fireEvent.click(await screen.findByRole("button", { name: "+ Назначить роль" }));
    expect(screen.queryByRole("option", { name: "Aurum Administrator" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

    await waitFor(() => expect(createAssignment).toHaveBeenCalledTimes(1));
    expect(createAssignment).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_id: null,
      password_required: false,
    });
  });

  it("protects owner membership from assignment and lifecycle actions", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update", "users.block", "users.delete", "roles.assign"],
    };
    listUsers.mockResolvedValue(
      usersResponse([
        {
          ...USER_ACTIVE,
          is_tenant_owner: true,
        },
      ]),
    );
    listRoles.mockResolvedValue([MANAGED_ROLE]);
    renderPage();

    expect(await screen.findByText("владелец")).toBeInTheDocument();
    await openUserActions();
    expect(screen.getByRole("menuitem", { name: "Профиль" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Роли" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Приостановить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Уволить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Завершить сеансы" })).not.toBeInTheDocument();
  });

  it("ends employee sessions only after an explicit confirmation", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.block"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Завершить сеансы" }));
    expect(screen.getByText(/будет немедленно выведен из системы/i)).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole("button", { name: "Завершить сеансы" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => expect(revokeUserSessions).toHaveBeenCalledWith("member-1"));
    expect(await screen.findByRole("status")).toHaveTextContent("Завершено активных сеансов: 1");
  });

  it("gates suspend and offboard with their own permissions", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.block"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    const firstRender = renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Приостановить" }));
    expect(screen.queryByRole("menuitem", { name: "Уволить" })).not.toBeInTheDocument();
    const suspendButtons = screen.getAllByRole("button", { name: "Приостановить" });
    fireEvent.click(suspendButtons[suspendButtons.length - 1]!);
    await waitFor(() => expect(suspendUser).toHaveBeenCalledWith("member-1"));

    firstRender.unmount();
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.delete"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    expect(await screen.findByRole("menuitem", { name: "Уволить" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Приостановить" })).not.toBeInTheDocument();
  });

  it("renders pagination and refetches the selected page", async () => {
    listUsers.mockResolvedValue({
      items: [USER_ACTIVE],
      total: 120,
      page: 1,
      page_size: 50,
    });
    renderPage();

    expect(await screen.findByText("Иван Сотрудник")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    await waitFor(() => expect(listUsers).toHaveBeenCalledWith(2, 50));
  });
});
