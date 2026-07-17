import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

vi.mock("@/features/roles/api", () => ({
  listUsers: vi.fn(async () => ({ items: [], total: 0, page: 1, page_size: 50 })),
  listRoles: vi.fn(async () => []),
  listPermissions: vi.fn(async () => []),
  listTemplates: vi.fn(async () => []),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  updateUser: vi.fn(),
  suspendUser: vi.fn(),
  offboardUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

import { RolesPage } from "@/features/roles/RolesPage";
import { UsersPage } from "@/features/roles/UsersPage";

function renderPage(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

const SELLER = { home_tenant_id: "tenant-1", permissions: ["pos.sell"] };
const USER_VIEWER = { home_tenant_id: "tenant-1", permissions: ["users.view"] };
const ROLE_ASSIGNER = { home_tenant_id: "tenant-1", permissions: ["roles.assign"] };
const ROLE_BUILDER = { home_tenant_id: "tenant-1", permissions: ["roles.create"] };

describe("UsersPage direct access", () => {
  beforeEach(() => {
    mockUser = {};
  });

  afterEach(() => vi.clearAllMocks());

  it("blocks a tenant user without users.view", () => {
    mockUser = SELLER;
    renderPage(<UsersPage />);

    expect(screen.getByText(/нет доступа к сотрудникам/i)).toBeInTheDocument();
  });

  it("allows users.view but never exposes an account creation action", async () => {
    mockUser = USER_VIEWER;
    renderPage(<UsersPage />);

    expect(await screen.findByText("Сотрудники")).toBeInTheDocument();
    expect(screen.queryByText(/Пригласить/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Создать аккаунт/i)).not.toBeInTheDocument();
  });

  it("blocks a support account without tenant context", () => {
    mockUser = {
      is_administrator: true,
      home_tenant_id: null,
      permissions: ["users.view"],
    };
    renderPage(<UsersPage />);

    expect(screen.getByText(/нет доступа к сотрудникам/i)).toBeInTheDocument();
  });
});

describe("RolesPage direct access", () => {
  beforeEach(() => {
    mockUser = {};
  });

  afterEach(() => vi.clearAllMocks());

  it("does not use users.view as a role-management permission", () => {
    mockUser = USER_VIEWER;
    renderPage(<RolesPage />);

    expect(screen.getByText(/нет доступа к управлению ролями/i)).toBeInTheDocument();
  });

  it("allows roles.assign to inspect manageable roles without showing the builder", async () => {
    mockUser = ROLE_ASSIGNER;
    renderPage(<RolesPage />);

    expect(await screen.findByText("Роли аптеки")).toBeInTheDocument();
    expect(screen.queryByText("+ Создать роль")).not.toBeInTheDocument();
  });

  it("shows the builder only with roles.create", async () => {
    mockUser = ROLE_BUILDER;
    renderPage(<RolesPage />);

    expect(await screen.findByText("+ Создать роль")).toBeInTheDocument();
  });
});
