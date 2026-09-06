import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listPermissions = vi.fn();
const listTemplates = vi.fn();
const createRole = vi.fn();
const updateRole = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listPermissions: (...args: unknown[]) => listPermissions(...args),
  listTemplates: (...args: unknown[]) => listTemplates(...args),
  createRole: (...args: unknown[]) => createRole(...args),
  updateRole: (...args: unknown[]) => updateRole(...args),
  listRoles: vi.fn(),
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  suspendUser: vi.fn(),
  offboardUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

import { RoleBuilderModal } from "@/features/roles/RoleBuilderModal";

const PERMISSIONS = [
  {
    code: "users.delete",
    group_code: "users",
    name: "Увольнение сотрудника",
    description: "Необратимо завершает работу сотрудника в аптеке.",
    is_dangerous: true,
    is_active: true,
    scope_type: "TENANT_ALL",
    target_role_type: "tenant",
    risk_level: "critical",
    requires_step_up: true,
    requires_confirmation: true,
  },
  {
    code: "pos.sell",
    group_code: "pos",
    name: "Продажа",
    description: "Продажа товаров на кассе и оформление чеков.",
    is_dangerous: false,
    is_active: true,
    scope_type: "BRANCH_SET",
    target_role_type: "tenant",
    risk_level: "normal",
    requires_step_up: false,
    requires_confirmation: false,
  },
];

function renderModal(
  props: ComponentProps<typeof RoleBuilderModal> = {
    mode: "create",
    onClose: () => {},
  },
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RoleBuilderModal {...props} />
    </QueryClientProvider>,
  );
}

describe("RoleBuilderModal", () => {
  beforeEach(() => {
    listPermissions.mockReset();
    listTemplates.mockReset();
    createRole.mockReset();
    updateRole.mockReset();
    listPermissions.mockResolvedValue(PERMISSIONS);
    listTemplates.mockResolvedValue([]);
    createRole.mockResolvedValue({});
  });

  afterEach(() => vi.clearAllMocks());

  it("renders exactly the server catalogue and marks dangerous permissions", async () => {
    renderModal();

    expect((await screen.findAllByText("Сотрудники")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Касса").length).toBeGreaterThan(0);
    expect(screen.getByText("Продажа товаров на кассе и оформление чеков.")).toBeInTheDocument();
    expect(screen.getByText("критично")).toBeInTheDocument();
    expect(screen.getByText("Подтверждение")).toBeInTheDocument();
    expect(screen.getByText("По точкам")).toBeInTheDocument();
    expect(screen.queryByText(/Уровень роли/i)).not.toBeInTheDocument();
  });

  it("drops inactive and platform permissions from a malformed catalogue response", async () => {
    listPermissions.mockResolvedValue([
      PERMISSIONS[1],
      {
        ...PERMISSIONS[1],
        code: "catalog.inactive",
        name: "Неактивная функция",
        is_active: false,
      },
      {
        ...PERMISSIONS[1],
        code: "platform.manage",
        name: "Управление платформой",
        target_role_type: "platform",
        scope_type: "PLATFORM",
      },
      {
        ...PERMISSIONS[1],
        code: "platform.malformed",
        name: "Некорректная платформенная функция",
        target_role_type: "tenant",
        scope_type: "PLATFORM",
      },
    ]);
    renderModal();

    expect(await screen.findByText("Продажа")).toBeInTheDocument();
    expect(screen.queryByText("Неактивная функция")).not.toBeInTheDocument();
    expect(screen.queryByText("Управление платформой")).not.toBeInTheDocument();
    expect(screen.queryByText("Некорректная платформенная функция")).not.toBeInTheDocument();
  });

  it("filters the server catalogue by a visible function group", async () => {
    renderModal();
    await screen.findByRole("checkbox", { name: /Продажа/ });

    fireEvent.click(screen.getByRole("button", { name: /Касса: 0 из 1 выбрано/ }));

    expect(screen.getByRole("checkbox", { name: /Продажа/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /Увольнение сотрудника/ }),
    ).not.toBeInTheDocument();
  });

  it("copies only catalogue permissions from a template", async () => {
    listPermissions.mockResolvedValue([PERMISSIONS[1]]);
    listTemplates.mockResolvedValue([
      {
        id: "tpl1",
        name: "Кассир",
        slug: "cashier",
        description: null,
        is_system: true,
        is_active: true,
        permissions: ["pos.sell", "users.delete"],
      },
    ]);
    renderModal();

    const sell = await screen.findByRole("checkbox", { name: /Продажа/ });
    fireEvent.change(await screen.findByLabelText(/Начать из шаблона/), {
      target: { value: "tpl1" },
    });

    expect(sell).toBeChecked();
    expect(screen.queryByText("Увольнение сотрудника")).not.toBeInTheDocument();
  });

  it("asks before a template replaces a manual permission selection", async () => {
    listTemplates.mockResolvedValue([
      {
        id: "tpl1",
        name: "Управляющий",
        slug: "manager",
        description: null,
        is_system: true,
        is_active: true,
        permissions: ["users.delete"],
      },
    ]);
    renderModal();

    const sell = await screen.findByRole("checkbox", { name: /Продажа/ });
    fireEvent.click(sell);
    fireEvent.change(screen.getByLabelText(/Начать из шаблона/), {
      target: { value: "tpl1" },
    });

    expect(screen.getByRole("dialog", { name: "Заменить выбранные функции?" })).toBeInTheDocument();
    expect(sell).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Применить шаблон" }));
    expect(screen.getByRole("checkbox", { name: /Увольнение сотрудника/ })).toBeChecked();
    expect(sell).not.toBeChecked();
  });

  it("does not infer extra permissions for a support account", async () => {
    listPermissions.mockResolvedValue([PERMISSIONS[1]]);
    renderModal();

    expect(await screen.findByText("Продажа")).toBeInTheDocument();
    expect(screen.queryByText("Увольнение сотрудника")).not.toBeInTheDocument();
  });

  it("searches the permission catalogue and selects a visible group", async () => {
    renderModal();
    await screen.findByRole("checkbox", { name: /Продажа/ });

    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск функций" }), {
      target: { value: "кассе" },
    });

    expect(screen.queryByText("Увольнение сотрудника")).not.toBeInTheDocument();
    const sell = screen.getByRole("checkbox", { name: /Продажа/ });
    fireEvent.click(screen.getByRole("button", { name: "Выбрать показанные" }));
    expect(sell).toBeChecked();
  });

  it("switches to a selected-only permission review", async () => {
    renderModal();
    const sell = await screen.findByRole("checkbox", { name: /Продажа/ });
    fireEvent.click(sell);
    fireEvent.click(screen.getByRole("button", { name: /Выбрано · 1/ }));

    expect(screen.getByRole("checkbox", { name: /Продажа/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /Увольнение сотрудника/ }),
    ).not.toBeInTheDocument();
  });

  it("submits a role without a numeric level", async () => {
    renderModal();
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "  Старший кассир  " },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Продажа/ }));
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    await waitFor(() => expect(createRole).toHaveBeenCalledTimes(1));
    expect(createRole).toHaveBeenCalledWith({
      name: "Старший кассир",
      description: null,
      permissions: ["pos.sell"],
    });
    expect(createRole.mock.calls[0]?.[0]).not.toHaveProperty("level");
  });

  it("does not create a role without any available function", async () => {
    renderModal();
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "Пустая роль" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    expect(
      await screen.findByText("Выберите хотя бы одну функцию, которая будет доступна сотруднику."),
    ).toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();
  });

  it("requires explicit confirmation before adding a dangerous permission", async () => {
    renderModal();
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "Управляющий" },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Увольнение сотрудника/ }));
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    expect(
      await screen.findByRole("dialog", { name: "Подтвердите расширение доступа" }),
    ).toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и сохранить" }));
    await waitFor(() => expect(createRole).toHaveBeenCalledTimes(1));
    expect(createRole).toHaveBeenCalledWith({
      name: "Управляющий",
      description: null,
      permissions: ["users.delete"],
    });
  });

  it("also confirms a permission marked requires_confirmation", async () => {
    listPermissions.mockResolvedValue([
      {
        ...PERMISSIONS[1],
        code: "reports.export",
        group_code: "reports",
        name: "Экспорт отчётов",
        is_dangerous: false,
        risk_level: "sensitive",
        requires_confirmation: true,
      },
    ]);
    renderModal();
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "Аналитик" },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Экспорт отчётов/ }));
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    expect(
      await screen.findByRole("dialog", { name: "Подтвердите расширение доступа" }),
    ).toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();
  });

  it("also confirms a permission that requires reauthentication when used", async () => {
    listPermissions.mockResolvedValue([
      {
        ...PERMISSIONS[1],
        code: "reports.export.full",
        group_code: "reports",
        name: "Полный экспорт",
        risk_level: "critical",
        requires_step_up: true,
        requires_confirmation: false,
      },
    ]);
    renderModal();
    fireEvent.change(await screen.findByLabelText("Название"), {
      target: { value: "Главный аналитик" },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Полный экспорт/ }));
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    expect(
      await screen.findByRole("dialog", { name: "Подтвердите расширение доступа" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/подтверждение паролем при использовании/)).toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();
  });

  it("returns to role details and focuses the first invalid field", async () => {
    renderModal();
    await screen.findByText("Продажа");
    fireEvent.click(screen.getByRole("button", { name: /Права доступа/ }));

    expect(screen.getByRole("button", { name: /Права доступа/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Создать роль" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "О роли" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveFocus());
    expect(screen.getByText("Введите название роли")).toBeInTheDocument();
    expect(createRole).not.toHaveBeenCalled();
  });

  it("shows a catalogue error and disables submission", async () => {
    listPermissions.mockRejectedValue(new Error("catalogue failed"));
    renderModal();

    expect(await screen.findByText(/Не удалось загрузить доступные функции/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Создать роль" })).toBeDisabled();
  });

  it("does not let the keyboard shortcut bypass a loading catalogue", () => {
    listPermissions.mockReturnValue(new Promise(() => {}));
    renderModal();

    const name = screen.getByLabelText("Название");
    fireEvent.change(name, { target: { value: "Новая роль" } });
    fireEvent.keyDown(name, { key: "Enter", ctrlKey: true });

    expect(createRole).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Создать роль" })).toBeDisabled();
  });

  it("blocks edit when the role contains a permission outside the grantable catalogue", async () => {
    listPermissions.mockResolvedValue([PERMISSIONS[1]]);
    renderModal({
      mode: "edit",
      role: {
        id: "role-1",
        tenant_id: "tenant-1",
        name: "Особая роль",
        description: null,
        is_system: false,
        is_protected: false,
        protected_kind: null,
        is_active: true,
        version: 1,
        permissions: ["pos.sell"],
        has_hidden_permissions: true,
      },
      onClose: () => {},
    });

    expect(
      await screen.findByText(
        "Роль содержит функции, недоступные для изменения. Редактирование заблокировано.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("audit.view.global")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Название")).toBeDisabled();
    const save = screen.getByRole("button", { name: "Проверить и опубликовать" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(updateRole).not.toHaveBeenCalled();
  });

  it("submits the expected role version so stale editors cannot overwrite changes", async () => {
    updateRole.mockResolvedValue({});
    renderModal({
      mode: "edit",
      role: {
        id: "role-1",
        tenant_id: "tenant-1",
        name: "Кассир",
        description: null,
        is_system: false,
        is_protected: false,
        protected_kind: null,
        is_active: true,
        version: 7,
        permissions: ["pos.sell"],
        has_hidden_permissions: false,
      },
      onClose: () => {},
    });

    await screen.findByText("Продажа");
    fireEvent.change(screen.getByLabelText(/Описание/), {
      target: { value: "Обновлённое описание" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Проверить и опубликовать" }));
    expect(
      await screen.findByRole("dialog", { name: "Опубликовать версию 8?" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать версию" }));

    await waitFor(() => expect(updateRole).toHaveBeenCalledTimes(1));
    expect(updateRole).toHaveBeenCalledWith("role-1", {
      expected_version: 7,
      name: "Кассир",
      description: "Обновлённое описание",
      permissions: ["pos.sell"],
    });
  });
});
