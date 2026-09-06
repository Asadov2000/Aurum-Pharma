import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AxiosError, type AxiosResponse } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listUsers = vi.fn();
const listRoles = vi.fn();
const listPermissions = vi.fn();
const updateUser = vi.fn();
const suspendUser = vi.fn();
const offboardUser = vi.fn();
const revokeUserSessions = vi.fn();
const reissueUserInvitation = vi.fn();
const inviteEmployee = vi.fn();
const createAssignment = vi.fn();
const replaceAssignments = vi.fn();
const listAssignmentHistory = vi.fn();
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
  reissueUserInvitation: (...args: unknown[]) => reissueUserInvitation(...args),
  inviteEmployee: (...args: unknown[]) => inviteEmployee(...args),
  createAssignment: (...args: unknown[]) => createAssignment(...args),
  replaceAssignments: (...args: unknown[]) => replaceAssignments(...args),
  listAssignmentHistory: (...args: unknown[]) => listAssignmentHistory(...args),
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
  invited_at: null,
  invitation_expires_at: null,
  invitation_status: null,
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

function employeeInviteConflict(): AxiosError {
  const response = {
    status: 409,
    data: {
      error: {
        code: "conflict",
        message: "Не удалось создать новый аккаунт с этим email",
        details: { reason: "email_unavailable" },
      },
    },
    statusText: "Conflict",
    headers: {},
    config: {},
  } as AxiosResponse;
  const error = new AxiosError("Request failed", "ERR_BAD_RESPONSE");
  error.response = response;
  return error;
}

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
    reissueUserInvitation.mockReset();
    inviteEmployee.mockReset();
    createAssignment.mockReset();
    replaceAssignments.mockReset();
    listAssignmentHistory.mockReset();
    createOwnershipTransfer.mockReset();
    listBranches.mockReset();
    listRoles.mockResolvedValue([MANAGED_ROLE, SYSTEM_ROLE]);
    listPermissions.mockResolvedValue([]);
    listBranches.mockResolvedValue([]);
    updateUser.mockResolvedValue({});
    suspendUser.mockResolvedValue(undefined);
    offboardUser.mockResolvedValue(undefined);
    revokeUserSessions.mockResolvedValue({ status: "ok", revoked_count: 1 });
    reissueUserInvitation.mockResolvedValue({
      invitation_id: "invitation-2",
      invitation_status: "pending",
      invited_at: "2026-08-30T09:00:00Z",
      invitation_expires_at: "2026-09-06T09:00:00Z",
    });
    inviteEmployee.mockResolvedValue({ id: "assignment-invited" });
    createAssignment.mockResolvedValue({ id: "assignment-new" });
    replaceAssignments.mockResolvedValue([{ id: "assignment-new" }]);
    listAssignmentHistory.mockResolvedValue([]);
    createOwnershipTransfer.mockResolvedValue({
      transfer: { id: "transfer-1" },
      sessions_revoked: false,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it("explains how the owner can add the first employee", async () => {
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    expect(await screen.findByText("Сотрудников пока нет")).toBeInTheDocument();
    expect(
      screen.getByText(/Добавить первого сотрудника может владелец аптеки/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Добавить сотрудника/i })).not.toBeInTheDocument();
  });

  it("lets the owner create an employee only with an available pharmacy role", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.invite", "roles.assign", "branches.view"],
    };
    listUsers.mockResolvedValue(usersResponse([]));
    listBranches.mockResolvedValue([
      {
        id: "branch-1",
        tenant_id: "tenant-1",
        name: "Аптека №1",
        address: "Душанбе",
        branch_type: "pharmacy",
        license_number: null,
        license_expires_at: null,
        working_hours: null,
        receipt_header: null,
        is_active: true,
        created_at: "2026-08-30T09:00:00Z",
        updated_at: "2026-08-30T09:00:00Z",
      },
    ]);
    renderPage();

    const addEmployeeButton = await screen.findByRole("button", {
      name: /Добавить сотрудника/i,
    });
    await waitFor(() => expect(addEmployeeButton).toBeEnabled());
    fireEvent.click(addEmployeeButton);
    expect(
      await screen.findByText(/Аккаунт будет привязан только к этой аптеке/i),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("ФИО сотрудника"), {
      target: { value: "Саида Каримова" },
    });
    fireEvent.change(screen.getByLabelText("Email для входа"), {
      target: { value: "saida@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText("Телефон (необязательно)"), {
      target: { value: "+992 90 000 00 00" },
    });
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.change(screen.getByLabelText("Доступ к торговым точкам"), {
      target: { value: "branch-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить данные" }));

    expect(await screen.findByText("Проверьте данные")).toBeInTheDocument();
    expect(screen.getByText("saida@aurum.tj")).toBeInTheDocument();
    expect(screen.getByText("Аптека №1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Создать доступ" }));

    await waitFor(() => expect(inviteEmployee).toHaveBeenCalledTimes(1));
    expect(inviteEmployee).toHaveBeenCalledWith({
      operation_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      email: "saida@aurum.tj",
      full_name: "Саида Каримова",
      phone: "+992 90 000 00 00",
      role_id: MANAGED_ROLE.id,
      branch_id: "branch-1",
      password_required: false,
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Доступ для «Саида Каримова» создан",
    );
  });

  it("keeps the employee form open and explains an existing email conflict", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.invite", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([]));
    inviteEmployee.mockRejectedValueOnce(employeeInviteConflict());
    renderPage();

    const addEmployeeButton = await screen.findByRole("button", {
      name: /Добавить сотрудника/i,
    });
    await waitFor(() => expect(addEmployeeButton).toBeEnabled());
    fireEvent.click(addEmployeeButton);
    fireEvent.change(screen.getByLabelText("ФИО сотрудника"), {
      target: { value: "Саида Каримова" },
    });
    fireEvent.change(screen.getByLabelText("Email для входа"), {
      target: { value: "existing@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.change(screen.getByLabelText("Доступ к торговым точкам"), {
      target: { value: "__all_branches__" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить данные" }));
    fireEvent.click(await screen.findByRole("button", { name: "Создать доступ" }));

    expect(
      await screen.findByText("Не удалось создать новый аккаунт с этим email"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Email для входа")).toHaveValue("existing@aurum.tj");
    expect(screen.getByRole("button", { name: "Проверить данные" })).toBeInTheDocument();
  });

  it("retries an uncertain employee creation with the same operation and locked data", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.invite", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([]));
    inviteEmployee.mockRejectedValueOnce(new AxiosError("Network Error", "ERR_NETWORK"));
    renderPage();

    const addEmployeeButton = await screen.findByRole("button", { name: /Добавить сотрудника/i });
    await waitFor(() => expect(addEmployeeButton).toBeEnabled());
    fireEvent.click(addEmployeeButton);
    fireEvent.change(screen.getByLabelText("ФИО сотрудника"), {
      target: { value: "Саида Каримова" },
    });
    fireEvent.change(screen.getByLabelText("Email для входа"), {
      target: { value: "saida@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: MANAGED_ROLE.id } });
    fireEvent.change(screen.getByLabelText("Доступ к торговым точкам"), {
      target: { value: "__all_branches__" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить данные" }));
    fireEvent.click(await screen.findByRole("button", { name: "Создать доступ" }));

    expect(await screen.findByText(/Не удалось получить результат/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Изменить данные" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Повторить создание" }));

    await waitFor(() => expect(inviteEmployee).toHaveBeenCalledTimes(2));
    expect(inviteEmployee.mock.calls[1]?.[0]).toEqual(inviteEmployee.mock.calls[0]?.[0]);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Доступ для «Саида Каримова» создан",
    );
  });

  it("requires an explicit branch scope before reviewing employee access", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.invite", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    const addEmployeeButton = await screen.findByRole("button", { name: /Добавить сотрудника/i });
    await waitFor(() => expect(addEmployeeButton).toBeEnabled());
    fireEvent.click(addEmployeeButton);
    fireEvent.change(screen.getByLabelText("ФИО сотрудника"), {
      target: { value: "Фаррух Саидов" },
    });
    fireEvent.change(screen.getByLabelText("Email для входа"), {
      target: { value: "farrukh@aurum.tj" },
    });
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить данные" }));

    expect(await screen.findByText("Выберите, к каким точкам дать доступ")).toBeInTheDocument();
    expect(inviteEmployee).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Доступ к торговым точкам"), {
      target: { value: "__all_branches__" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить данные" }));
    expect(await screen.findByText(/доступ ко всем текущим и будущим точкам/i)).toBeInTheDocument();
  });

  it("does not show employee creation to a non-owner with delegated permissions", async () => {
    mockUser = {
      id: "manager",
      home_tenant_id: "tenant-1",
      is_tenant_owner: false,
      permissions: ["users.view", "users.invite", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    expect(await screen.findByText("Сотрудников пока нет")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Добавить сотрудника/i })).not.toBeInTheDocument();
  });

  it("does not show employee creation during scoped support access", async () => {
    mockUser = {
      id: "support-admin",
      active_tenant_id: "tenant-1",
      home_tenant_id: null,
      is_tenant_owner: false,
      permissions: ["users.view", "users.invite", "roles.assign"],
      support_access: {
        id: "support-session-1",
        tenant_id: "tenant-1",
        tenant_name: "Аптека Сино",
        reason: "Проверка учетных записей",
        capabilities: ["users.view", "users.invite", "roles.assign"],
        is_read_only: false,
        expires_at: "2030-01-01T00:00:00Z",
      },
    };
    listUsers.mockResolvedValue(usersResponse([]));
    renderPage();

    expect(await screen.findByText("Сотрудников пока нет")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Добавить сотрудника/i })).not.toBeInTheDocument();
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
    expect(screen.queryByRole("menuitem", { name: "Настроить доступ" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("menuitem", { name: "Настроить доступ" })).toBeInTheDocument();
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

  it("does not allow an owner to activate a pending membership manually", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.update"],
    };
    listUsers.mockResolvedValue(
      usersResponse([
        {
          ...USER_ACTIVE,
          status: "pending",
          invitation_status: "pending",
          invitation_expires_at: "2026-09-06T09:00:00Z",
          assignments: [],
        },
      ]),
    );
    renderPage();

    await openUserActions();
    expect(screen.queryByRole("menuitem", { name: "Активировать" })).not.toBeInTheDocument();
    expect(updateUser).not.toHaveBeenCalled();
  });

  it("reissues only an expired invitation through users.invite", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "users.invite"],
    };
    listUsers.mockResolvedValue(
      usersResponse([
        {
          ...USER_ACTIVE,
          status: "pending",
          invitation_status: "expired",
          invitation_expires_at: "2026-08-29T09:00:00Z",
          assignments: [],
        },
      ]),
    );
    renderPage();

    expect(await screen.findByText("Приглашение истекло")).toBeInTheDocument();
    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Обновить приглашение" }));

    await waitFor(() =>
      expect(reissueUserInvitation).toHaveBeenCalledWith("member-1", expect.any(String)),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Приглашение для");
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
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
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
    expect(replaceAssignments).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Применить доступ" }));

    await waitFor(() => expect(replaceAssignments).toHaveBeenCalledTimes(1));
    expect(replaceAssignments).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_ids: [null],
      password_required: false,
      replace_all: false,
    });
  });

  it("allows roles to be prepared before the first confirmed login", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listUsers.mockResolvedValue(
      usersResponse([
        {
          ...USER_ACTIVE,
          status: "pending",
          invitation_status: "pending",
          invitation_expires_at: "2026-09-06T09:00:00Z",
          assignments: [],
        },
      ]),
    );
    renderPage();

    expect(
      await screen.findByText(/Начнёт действовать после первого подтверждённого входа/),
    ).toBeInTheDocument();
    await openUserActions();
    expect(await screen.findByRole("menuitem", { name: "Настроить доступ" })).toBeInTheDocument();
  });

  it("applies one role to multiple selected branches in one request", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listBranches.mockResolvedValue([
      { id: "branch-1", name: "Аптека Рудаки", is_active: true },
      { id: "branch-2", name: "Аптека Сино", is_active: true },
    ]);
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: MANAGED_ROLE.id } });
    fireEvent.click(screen.getByRole("button", { name: "В выбранных точках" }));
    fireEvent.click(screen.getByLabelText("Аптека Рудаки"));
    fireEvent.click(screen.getByLabelText("Аптека Сино"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(within(confirmation).getByText("Аптека Рудаки, Аптека Сино")).toBeInTheDocument();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Применить доступ" }));

    await waitFor(() => expect(replaceAssignments).toHaveBeenCalledTimes(1));
    expect(replaceAssignments).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_ids: ["branch-1", "branch-2"],
      password_required: false,
      replace_all: false,
    });
  });

  it("transfers access and revokes unselected assignments only after confirmation", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listBranches.mockResolvedValue([
      { id: "branch-1", name: "Аптека Рудаки", is_active: true },
      { id: "branch-2", name: "Аптека Сино", is_active: true },
    ]);
    const assignments = ["branch-1", "branch-2"].map((branchId, index) => ({
      ...USER_ACTIVE.assignments[0],
      id: `assignment-${index + 1}`,
      branch_id: branchId,
    }));
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: MANAGED_ROLE.id } });
    fireEvent.click(screen.getByRole("button", { name: "В выбранных точках" }));
    fireEvent.click(screen.getByLabelText("Аптека Сино"));
    fireEvent.click(screen.getByLabelText("Оставить только выбранный доступ"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(within(confirmation).getByText("Другой доступ будет отозван")).toBeInTheDocument();
    expect(within(confirmation).getByText(/Аптека Рудаки: «Кассир»/)).toBeInTheDocument();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Перевести и применить" }));

    await waitFor(() => expect(replaceAssignments).toHaveBeenCalledTimes(1));
    expect(replaceAssignments).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_ids: ["branch-2"],
      password_required: false,
      replace_all: true,
    });
  });

  it("shows disabled branch access as paused and excludes the branch from new assignments", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listBranches.mockResolvedValue([
      { id: "branch-closed", name: "Аптека Закрытая", is_active: false },
    ]);
    listUsers.mockResolvedValue(
      usersResponse([
        {
          ...USER_ACTIVE,
          assignments: [{ ...USER_ACTIVE.assignments[0], branch_id: "branch-closed" }],
        },
      ]),
    );
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    expect(await screen.findByText("приостановлена", {}, LAZY_PANEL_WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Аптека Закрытая · точка отключена/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Назначить роль" }));
    fireEvent.click(screen.getByRole("button", { name: "В выбранных точках" }));
    expect(screen.queryByLabelText("Аптека Закрытая")).not.toBeInTheDocument();
    expect(screen.getByText("Активных торговых точек пока нет.")).toBeInTheDocument();
    expect(await screen.findByText("Журнал изменений доступа · 0")).toBeInTheDocument();
  });

  it("shows a focused access history without raw audit data", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    listAssignmentHistory.mockResolvedValue([
      {
        id: "history-1",
        event_type: "assigned",
        actor_name: "Владелец аптеки",
        role_id: MANAGED_ROLE.id,
        role_name: MANAGED_ROLE.name,
        branch_id: null,
        branch_name: null,
        password_required: false,
        is_active: true,
        created_at: "2026-09-01T08:30:00Z",
      },
    ]);
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByText("Журнал изменений доступа · 1", {}, LAZY_PANEL_WAIT));

    expect(screen.getByText("Назначено")).toBeInTheDocument();
    expect(screen.getByText(/Вся аптека · Владелец аптеки/)).toBeInTheDocument();
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
    listBranches.mockResolvedValue([{ id: "branch-1", name: "Аптека Рудаки", is_active: true }]);
    listUsers.mockResolvedValue(usersResponse([{ ...USER_ACTIVE, assignments: [] }]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), { target: { value: reviewRole.id } });
    fireEvent.click(screen.getByRole("button", { name: "В выбранных точках" }));
    fireEvent.click(screen.getByLabelText("Аптека Рудаки"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(within(confirmation).getByText("Старший кассир")).toBeInTheDocument();
    expect(within(confirmation).getByText("Аптека Рудаки")).toBeInTheDocument();
    expect(within(confirmation).getByText(/Разделы: Касса/)).toBeInTheDocument();
    expect(within(confirmation).getByText("Проводить продажи")).toBeInTheDocument();
    expect(within(confirmation).getByText("Отключать сотрудников от аптеки")).toBeInTheDocument();
    expect(within(confirmation).getByText("Не включатся для отдельных точек")).toBeInTheDocument();
    expect(within(confirmation).queryByText("Обратите внимание")).not.toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));

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
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
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
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByLabelText("Запрашивать пароль при входе с этой ролью"));
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));
    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Применить доступ" }));

    await waitFor(() => expect(replaceAssignments).toHaveBeenCalledTimes(1));
    expect(replaceAssignments).toHaveBeenCalledWith("member-1", {
      role_id: MANAGED_ROLE.id,
      branch_ids: [null],
      password_required: true,
      replace_all: false,
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
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: MANAGED_ROLE.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    expect(
      await screen.findByText(
        "Эта роль с такими настройками уже действует во всех выбранных областях",
      ),
    ).toBeInTheDocument();
    expect(replaceAssignments).not.toHaveBeenCalled();
  });

  it("replaces an occupied scope atomically after explicit confirmation", async () => {
    mockUser = {
      id: "current-owner",
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["users.view", "roles.assign"],
    };
    const replacementRole = {
      ...MANAGED_ROLE,
      id: "role-replacement",
      name: "Фармацевт",
    };
    listRoles.mockResolvedValue([MANAGED_ROLE, replacementRole]);
    listUsers.mockResolvedValue(usersResponse([USER_ACTIVE]));
    renderPage();

    await openUserActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Настроить доступ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Назначить роль" }, LAZY_PANEL_WAIT));
    fireEvent.change(screen.getByLabelText("Роль"), {
      target: { value: replacementRole.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить доступ" }));

    const confirmation = await screen.findByRole("dialog", {
      name: "Проверьте доступ сотрудника",
    });
    expect(
      within(confirmation).getByText("Роль будет заменена без разрыва доступа"),
    ).toBeInTheDocument();
    expect(within(confirmation).getByText(/«Кассир» → «Фармацевт»/)).toBeInTheDocument();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Заменить и применить" }));

    await waitFor(() => expect(replaceAssignments).toHaveBeenCalledTimes(1));
    expect(replaceAssignments).toHaveBeenCalledWith("member-1", {
      role_id: replacementRole.id,
      branch_ids: [null],
      password_required: false,
      replace_all: false,
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
    expect(screen.queryByRole("menuitem", { name: "Настроить доступ" })).not.toBeInTheDocument();
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
