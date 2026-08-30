import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listUsers = vi.fn();
const listRoles = vi.fn();
const listPermissions = vi.fn();
const updateUser = vi.fn();
const suspendUser = vi.fn();
const offboardUser = vi.fn();
const revokeUserSessions = vi.fn();
const createAssignment = vi.fn();
const createOwnershipTransfer = vi.fn();
const listBranches = vi.fn();
const LAZY_PANEL_WAIT = { timeout: 5_000 };

vi.mock("@/features/roles/api", () => ({
  listUsers: (...args: unknown[]) => listUsers(...args),
  listRoles: (...args: unknown[]) => listRoles(...args),
  listPermissions: (...args: unknown[]) => listPermissions(...args),
  listTemplates: vi.fn().mockResolvedValue([]),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  updateUser: (...args: unknown[]) => updateUser(...args),
  suspendUser: (...args: unknown[]) => suspendUser(...args),
  offboardUser: (...args: unknown[]) => offboardUser(...args),
  revokeUserSessions: (...args: unknown[]) => revokeUserSessions(...args),
  createAssignment: (...args: unknown[]) => createAssignment(...args),
  createOwnershipTransfer: (...args: unknown[]) => createOwnershipTransfer(...args),
  listOwnershipTransfers: vi.fn().mockResolvedValue([]),
  cancelOwnershipTransfer: vi.fn(),
  acceptOwnershipTransfer: vi.fn(),
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

const SELL_PERMISSION = {
  code: "pos.sell",
  group_code: "pos",
  name: "Проводить продажи",
  description: "Создание продажи на кассе",
  is_dangerous: false,
  is_active: true,
  scope_type: "BRANCH_SET" as const,
  target_role_type: "tenant" as const,
  risk_level: "normal" as const,
  requires_step_up: false,
  requires_confirmation: false,
};

const DELETE_USER_PERMISSION = {
  ...SELL_PERMISSION,
  code: "users.delete",
  group_code: "users",
  name: "Отключать сотрудников от аптеки",
  description: "Прекращение доступа сотрудника",
  is_dangerous: true,
  scope_type: "TENANT_ALL" as const,
  risk_level: "critical" as const,
  requires_confirmation: true,
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
  can_require_password: false,
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
    window.localStorage.clear();
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view"],
    };
    listUsers.mockReset();
    listRoles.mockReset();
    listPermissions.mockReset();
    updateUser.mockReset();
    suspendUser.mockReset();
    offboardUser.mockReset();
    revokeUserSessions.mockReset();
    createAssignment.mockReset();
    createOwnershipTransfer.mockReset();
    listBranches.mockReset();
    listRoles.mockResolvedValue([MANAGED_ROLE, SYSTEM_ROLE]);
    listPermissions.mockResolvedValue([]);
    listBranches.mockResolvedValue([]);
    updateUser.mockResolvedValue({});
    suspendUser.mockResolvedValue(undefined);
    offboardUser.mockResolvedValue(undefined);
    revokeUserSessions.mockResolvedValue({ status: "ok", revoked_count: 1 });
    createAssignment.mockResolvedValue({ id: "assignment-new" });
    createOwnershipTransfer.mockResolvedValue({
      transfer: { id: "transfer-1" },
      sessions_revoked: false,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it("renders attached-membership empty state without invite controls", async () => {
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    expect(await screen.findByText("Сотрудников пока нет")).toBeInTheDocument();
    expect(
      screen.getByText(/Аккаунты сотрудников создаёт и подключает команда Aurum Pharma/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/владелец аптеки назначает каждому сотруднику/i)).toBeInTheDocument();
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
    mockUser = {
      id: "regular-user",
      home_tenant_id: "tenant-1",
      is_tenant_owner: false,
      permissions: ["users.view"],
    };
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

  it("lets only the owner request ownership transfer after explicit confirmation", async () => {
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Передать владение" }));
    expect(screen.getByText("Передать владение аптекой?")).toBeInTheDocument();
    expect(screen.getByText(/при следующем входе потребуется настроить MFA/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Отправить запрос" }));

    await waitFor(() => expect(createOwnershipTransfer).toHaveBeenCalledTimes(1));
    expect(createOwnershipTransfer).toHaveBeenCalledWith({
      operation_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      target_membership_id: USER_ACTIVE.membership_id,
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "До подтверждения вы остаётесь владельцем",
    );
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
    fireEvent.change(await screen.findByLabelText("ФИО", undefined, LAZY_PANEL_WAIT), {
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
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    expect(screen.queryByRole("option", { name: "Aurum Administrator" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(within(confirmation).getByText("Кассир")).toBeInTheDocument();
    expect(within(confirmation).getByText("Все точки аптеки")).toBeInTheDocument();
    expect(createAssignment).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Назначить роль" }));

    await waitFor(() => expect(createAssignment).toHaveBeenCalledTimes(1));
    expect(createAssignment).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_id: null,
      password_required: false,
    });
  });

  it("reviews only visible capabilities and warns about important permissions", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    const reviewRole = {
      ...MANAGED_ROLE,
      id: "role-review",
      name: "Старший кассир",
      permissions: ["pos.sell", "users.delete", "platform.secret", "roles.not-visible"],
    };
    listRoles.mockResolvedValue([reviewRole]);
    listPermissions.mockResolvedValue([
      SELL_PERMISSION,
      DELETE_USER_PERMISSION,
      {
        ...SELL_PERMISSION,
        code: "platform.secret",
        name: "Системное управление Aurum",
        scope_type: "PLATFORM",
        target_role_type: "platform",
      },
    ]);
    listBranches.mockResolvedValue([{ id: "branch-1", name: "Аптека Рудаки" }]);
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: reviewRole.id } });
    fireEvent.change(screen.getByLabelText("Где действует роль"), {
      target: { value: "branch-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(within(confirmation).getByText("Старший кассир")).toBeInTheDocument();
    expect(within(confirmation).getByText("Аптека Рудаки")).toBeInTheDocument();
    expect(within(confirmation).getByText(/Разделы: Касса, Сотрудники/)).toBeInTheDocument();
    expect(within(confirmation).getByText("Проводить продажи")).toBeInTheDocument();
    expect(within(confirmation).getByText("Отключать сотрудников от аптеки")).toBeInTheDocument();
    expect(within(confirmation).getByText("Обратите внимание")).toBeInTheDocument();
    expect(within(confirmation).queryByText("Системное управление Aurum")).not.toBeInTheDocument();
    expect(within(confirmation).queryByText("roles.not-visible")).not.toBeInTheDocument();
  });

  it("does not offer a role whose permissions are only partially visible", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listRoles.mockResolvedValue([
      {
        ...MANAGED_ROLE,
        id: "role-hidden-capabilities",
        name: "Неполная роль",
        has_hidden_permissions: true,
      },
    ]);
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));

    expect(
      await screen.findByText("Нет доступных для назначения ролей.", {}, LAZY_PANEL_WAIT),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Назначить роль" })).not.toBeInTheDocument();
  });

  it("does not offer password enforcement before the employee configures a password", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));

    expect(
      screen.queryByLabelText("Запрашивать пароль при входе с этой ролью"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Обязательный пароль станет доступен после настройки пароля сотрудником."),
    ).toBeInTheDocument();
  });

  it("allows password enforcement after the employee configures a password", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listUsers.mockResolvedValue(
      usersResponse([{ ...USER_ACTIVE, can_require_password: true, assignments: [] }]),
    );
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Роли" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByLabelText("Запрашивать пароль при входе с этой ролью"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));
    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Назначить роль" }));

    await waitFor(() => expect(createAssignment).toHaveBeenCalledTimes(1));
    expect(createAssignment).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_id: null,
      password_required: true,
    });
  });

  it("does not submit a duplicate active role for the same scope", async () => {
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
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    expect(
      await screen.findByText("Эта роль уже назначена сотруднику для выбранной области"),
    ).toBeInTheDocument();
    expect(createAssignment).not.toHaveBeenCalled();
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
    await waitFor(() =>
      expect(listUsers).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, page_size: 25 }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("keeps a dense access summary when an employee has many roles", async () => {
    const assignments = ["Кассир", "Фармацевт", "Кладовщик", "Менеджер"].map((name, index) => ({
      ...USER_ACTIVE.assignments[0],
      id: `assignment-${index}`,
      role_id: `role-${index}`,
      role_name: name,
    }));
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments }]));
    renderPage();

    expect(await screen.findByText("Кассир")).toBeInTheDocument();
    expect(screen.getByText("Фармацевт")).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.queryByText("Кладовщик")).not.toBeInTheDocument();
    expect(screen.queryByText("Менеджер")).not.toBeInTheDocument();
  });

  it("warns before discarding profile edits and returns focus to the employee actions", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    const actionTrigger = await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Профиль" }));
    fireEvent.change(await screen.findByLabelText("ФИО", undefined, LAZY_PANEL_WAIT), {
      target: { value: "Иван Несохранённый" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

    const discardDialog = await screen.findByRole("dialog", { name: "Отменить изменения?" });
    fireEvent.click(within(discardDialog).getByRole("button", { name: "Выйти без сохранения" }));

    await waitFor(() => expect(actionTrigger).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: /Профиль:/ })).not.toBeInTheDocument();
  });

  it("offers a retry after the employee directory fails to load", async () => {
    listUsers
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce(usersResponse([USER_ACTIVE]));
    renderPage();

    expect(await screen.findByText("Не удалось загрузить сотрудников")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("Иван Сотрудник")).toBeInTheDocument();
    expect(listUsers).toHaveBeenCalledTimes(2);
  });

  it("debounces private search values and sends filters in the request body contract", async () => {
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();
    await screen.findByText("Иван Сотрудник");

    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "  Иван  " },
    });
    fireEvent.change(screen.getByLabelText("Статус"), {
      target: { value: "active" },
    });

    await waitFor(() =>
      expect(listUsers).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "Иван",
          status: "active",
          page: 1,
          page_size: 25,
        }),
        expect.any(AbortSignal),
      ),
    );
  });
});
