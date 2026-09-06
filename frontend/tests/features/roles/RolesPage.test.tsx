import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listRoles = vi.fn();
const listPermissions = vi.fn();
const listTemplates = vi.fn();
const createRole = vi.fn();
const updateRole = vi.fn();
const listRoleVersions = vi.fn();
const archiveRole = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listRoles: (...args: unknown[]) => listRoles(...args),
  listPermissions: (...args: unknown[]) => listPermissions(...args),
  listTemplates: (...args: unknown[]) => listTemplates(...args),
  createRole: (...args: unknown[]) => createRole(...args),
  updateRole: (...args: unknown[]) => updateRole(...args),
  listRoleVersions: (...args: unknown[]) => listRoleVersions(...args),
  archiveRole: (...args: unknown[]) => archiveRole(...args),
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

vi.mock("@/components/AccessDeniedCard", () => ({
  AccessDeniedCard: ({ message }: { message: string }) => <div>{message}</div>,
}));

import { RoleBuilderLoadBoundary, RolesPage } from "@/features/roles/RolesPage";

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
  has_hidden_permissions: false,
  active_assignment_count: 2,
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
      is_tenant_owner: true,
      permissions: ["roles.update"],
    };
    listRoles.mockReset();
    listPermissions.mockReset();
    listTemplates.mockReset();
    createRole.mockReset();
    updateRole.mockReset();
    listRoleVersions.mockReset();
    archiveRole.mockReset();
    listRoles.mockResolvedValue([MANAGED_ROLE, SYSTEM_ROLE, PROTECTED_ROLE, FOREIGN_ROLE]);
    listTemplates.mockResolvedValue([]);
    listRoleVersions.mockResolvedValue([]);
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
      is_tenant_owner: true,
      permissions: ["roles.create"],
    };
    renderPage();

    expect(await screen.findByText("Старший кассир")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Создать роль" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
  });

  it("requires update and assignment access before offering role archival", async () => {
    const firstRender = renderPage();
    expect(await screen.findByText("Старший кассир")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
    firstRender.unmount();

    mockUser = {
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["roles.update", "roles.assign"],
    };
    listRoles.mockResolvedValue([
      MANAGED_ROLE,
      { ...MANAGED_ROLE, id: "replacement", name: "Фармацевт" },
      {
        ...MANAGED_ROLE,
        id: "hidden-replacement",
        name: "Скрытая роль",
        has_hidden_permissions: true,
      },
    ]);
    renderPage();

    const archiveButtons = await screen.findAllByRole("button", { name: "Архивировать" });
    fireEvent.click(archiveButtons[0]);
    expect(screen.getByRole("option", { name: "Фармацевт" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Скрытая роль" })).not.toBeInTheDocument();
  });

  it("shows the publication author, timestamps, and exact permission changes", async () => {
    listRoleVersions.mockResolvedValue([
      {
        id: "version-2",
        role_id: MANAGED_ROLE.id,
        version: 2,
        name: MANAGED_ROLE.name,
        description: "Разрешена продажа",
        status: "published",
        permissions: ["pos.sell"],
        published_at: "2030-01-02T10:00:00Z",
        archived_at: null,
        created_at: "2030-01-02T10:00:00Z",
        created_by: "owner-1",
        created_by_name: "Фарход И.",
      },
      {
        id: "version-1",
        role_id: MANAGED_ROLE.id,
        version: 1,
        name: MANAGED_ROLE.name,
        description: null,
        status: "archived",
        permissions: [],
        published_at: "2030-01-01T10:00:00Z",
        archived_at: "2030-01-02T10:00:00Z",
        created_at: "2030-01-01T10:00:00Z",
        created_by: "owner-1",
        created_by_name: "Фарход И.",
      },
    ]);
    renderPage();

    await screen.findByText("Старший кассир");
    fireEvent.click(screen.getByRole("button", { name: "История" }));

    expect(await screen.findByText("Версия 2")).toBeInTheDocument();
    expect(screen.getAllByText("Фарход И.")).toHaveLength(2);
    expect(screen.getByText(/Продажа/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Разрешена продажа")).toBeInTheDocument();
  });

  it("does not request the constructor catalogue for an assignment-only owner", async () => {
    mockUser = {
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["roles.assign"],
    };
    renderPage();

    expect(await screen.findByText("Старший кассир")).toBeInTheDocument();
    expect(listPermissions).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Создать роль" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
  });

  it("does not expose the constructor to a non-owner with a copied permission", () => {
    mockUser = {
      home_tenant_id: "tenant-1",
      is_tenant_owner: false,
      permissions: ["roles.update"],
    };
    renderPage();

    expect(
      screen.getByText("У вас нет доступа к управлению ролями этой аптеки."),
    ).toBeInTheDocument();
    expect(listRoles).not.toHaveBeenCalled();
    expect(listPermissions).not.toHaveBeenCalled();
  });

  it("does not expose the role catalog to scoped support with only users.view", () => {
    mockUser = {
      active_tenant_id: "tenant-1",
      home_tenant_id: null,
      is_developer: true,
      is_administrator: false,
      is_tenant_owner: false,
      permissions: ["users.view"],
      support_access: {
        id: "support-session-1",
        tenant_id: "tenant-1",
        tenant_name: "Аптека Сино",
        reason: "Проверка учетных записей",
        capabilities: ["users.view"],
        is_read_only: true,
        expires_at: "2030-01-01T00:00:00Z",
      },
    };
    renderPage();

    expect(screen.getByText(/нет доступа к управлению ролями/i)).toBeInTheDocument();
    expect(listRoles).not.toHaveBeenCalled();
    expect(listPermissions).not.toHaveBeenCalled();
  });

  it("renders a request error without an empty-state fallback", async () => {
    listRoles.mockRejectedValue(new Error("roles failed"));
    renderPage();

    expect(await screen.findByText(/Не удалось загрузить список/i)).toBeInTheDocument();
    expect(screen.queryByText(/Управляемых ролей пока нет/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(listRoles).toHaveBeenCalledTimes(2));
  });

  it("filters the visible tenant roles without exposing protected roles", async () => {
    listRoles.mockResolvedValue([
      MANAGED_ROLE,
      { ...MANAGED_ROLE, id: "role-2", name: "Фармацевт" },
      SYSTEM_ROLE,
    ]);
    renderPage();

    await screen.findByText("Старший кассир");
    fireEvent.change(screen.getByLabelText("Поиск"), {
      target: { value: "фарма" },
    });

    expect(screen.getByText("Фармацевт")).toBeInTheDocument();
    expect(screen.queryByText("Старший кассир")).not.toBeInTheDocument();
    expect(screen.queryByText("Aurum Administrator")).not.toBeInTheDocument();
  });

  it("blocks editing when a role contains a code outside the loaded catalogue", async () => {
    listRoles.mockResolvedValue([
      {
        ...MANAGED_ROLE,
        permissions: ["pos.sell", "users.delete"],
        has_hidden_permissions: false,
      },
    ]);
    renderPage();

    expect(await screen.findByText(/Изменение заблокировано/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Изменить" })).toBeDisabled();
    expect(screen.queryByText("users.delete")).not.toBeInTheDocument();
  });

  it("fails closed when a tenant catalogue item has platform scope", async () => {
    listPermissions.mockResolvedValue([
      {
        code: "pos.sell",
        group_code: "pos",
        name: "Продажа",
        description: null,
        is_dangerous: false,
        is_active: true,
        scope_type: "PLATFORM",
        target_role_type: "tenant",
        risk_level: "normal",
        requires_step_up: false,
        requires_confirmation: false,
      },
    ]);
    renderPage();

    expect(await screen.findByText(/Изменение заблокировано/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Изменить" })).toBeDisabled();
  });

  it("paginates long role lists without rendering every card", async () => {
    listRoles.mockResolvedValue(
      Array.from({ length: 9 }, (_, index) => ({
        ...MANAGED_ROLE,
        id: `role-${index + 1}`,
        name: `Роль ${index + 1}`,
      })),
    );
    renderPage();

    expect(await screen.findByText("Роль 1")).toBeInTheDocument();
    expect(screen.queryByText("Роль 9")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    expect(screen.getByText("Роль 9")).toBeInTheDocument();
    expect(screen.queryByText("Роль 1")).not.toBeInTheDocument();
  });

  it("asks before closing a role draft with unsaved changes", async () => {
    mockUser = {
      home_tenant_id: "tenant-1",
      is_tenant_owner: true,
      permissions: ["roles.create", "roles.update"],
    };
    renderPage();

    await screen.findByText("Старший кассир");
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));
    fireEvent.change(await screen.findByLabelText("Название", undefined, { timeout: 5_000 }), {
      target: { value: "Новая роль" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

    expect(await screen.findByRole("dialog", { name: "Отменить изменения?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Продолжить редактирование" }));
    expect(screen.getByRole("dialog", { name: "Создать роль" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    fireEvent.click(screen.getByRole("button", { name: "Выйти без сохранения" }));
    expect(screen.queryByRole("dialog", { name: "Создать роль" })).not.toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();
  });
});

function BrokenRoleBuilder(): ReactNode {
  throw new Error("chunk failed");
}

describe("RoleBuilderLoadBoundary", () => {
  it("contains a failed lazy builder instead of crashing the roles page", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const onClose = vi.fn();

    try {
      render(
        <RoleBuilderLoadBoundary onClose={onClose}>
          <BrokenRoleBuilder />
        </RoleBuilderLoadBoundary>,
      );

      expect(screen.getByRole("alert")).toHaveTextContent("Конструктор не загрузился");
      fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      consoleError.mockRestore();
    }
  });
});
