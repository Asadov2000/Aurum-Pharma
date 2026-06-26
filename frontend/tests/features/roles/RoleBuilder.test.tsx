import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listPermissions = vi.fn();
const listTemplates = vi.fn();
const createRole = vi.fn();
const updateRole = vi.fn();

vi.mock("@/features/roles/api", () => ({
  listPermissions: (...a: unknown[]) => listPermissions(...a),
  listTemplates: (...a: unknown[]) => listTemplates(...a),
  createRole: (...a: unknown[]) => createRole(...a),
  updateRole: (...a: unknown[]) => updateRole(...a),
  // unused here but imported by queries.ts
  listRoles: vi.fn(),
  listUsers: vi.fn(),
  inviteUser: vi.fn(),
  updateUser: vi.fn(),
  blockUser: vi.fn(),
  archiveUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({ useAuth: () => ({ user: mockUser }) }));

import { RoleBuilderModal } from "@/features/roles/RoleBuilderModal";

const PERMS = [
  {
    code: "users.invite",
    group_code: "users",
    name: "Приглашение сотрудника",
    description: "Приглашение нового сотрудника по email.",
    min_level_required: 3,
    is_dangerous: false,
    is_active: true,
  },
  {
    code: "pos.sell",
    group_code: "pos",
    name: "Продажа",
    description: "Продажа товаров на кассе и оформление чеков.",
    min_level_required: 4,
    is_dangerous: false,
    is_active: true,
  },
];

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RoleBuilderModal mode="create" onClose={() => {}} />
    </QueryClientProvider>,
  );
}

describe("RoleBuilderModal", () => {
  beforeEach(() => {
    listPermissions.mockResolvedValue(PERMS);
    listTemplates.mockResolvedValue([]);
    createRole.mockReset();
    updateRole.mockReset();
    mockUser = { is_developer: true };
  });
  afterEach(() => vi.clearAllMocks());

  it("groups functions by section with a short description under each", async () => {
    mockUser = { is_developer: true }; // support sees everything
    renderModal();
    // Russian section headings
    expect(await screen.findByText("Сотрудники")).toBeInTheDocument();
    expect(screen.getByText("Касса")).toBeInTheDocument();
    // a function name + its tooltip/description text
    expect(screen.getByText("Продажа")).toBeInTheDocument();
    expect(
      screen.getByText("Продажа товаров на кассе и оформление чеков."),
    ).toBeInTheDocument();
  });

  it("prefills the checkboxes from a chosen template", async () => {
    mockUser = { is_developer: true };
    listTemplates.mockResolvedValue([
      {
        id: "tpl1",
        name: "Кассир",
        slug: "cashier",
        description: null,
        is_system: true,
        is_active: true,
        permissions: ["pos.sell"],
      },
    ]);
    renderModal();
    const sell = await screen.findByRole("checkbox", { name: /Продажа/ });
    expect(sell).not.toBeChecked();

    fireEvent.change(await screen.findByLabelText(/Начать из шаблона/), {
      target: { value: "tpl1" },
    });

    expect(screen.getByRole("checkbox", { name: /Продажа/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Приглашение сотрудника/ })).not.toBeChecked();
  });

  it("shows only the functions the current user holds (anti-escalation)", async () => {
    // A limited user holding only pos.sell must not even see users.invite.
    mockUser = { home_tenant_id: "t-1", permissions: ["pos.sell"] };
    renderModal();
    expect(await screen.findByText("Продажа")).toBeInTheDocument();
    expect(screen.queryByText("Приглашение сотрудника")).not.toBeInTheDocument();
  });

  it("hides functions above the role level", async () => {
    mockUser = {
      home_tenant_id: "t-1",
      level: 3,
      permissions: ["pos.sell", "users.invite"],
    };
    renderModal();
    expect(await screen.findByText("Продажа")).toBeInTheDocument();
    expect(screen.queryByText("Приглашение сотрудника")).not.toBeInTheDocument();
  });
});
