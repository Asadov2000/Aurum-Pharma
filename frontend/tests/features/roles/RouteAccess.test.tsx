import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Render-time Links (in the access-denied card) need a router context; stub
// them to plain anchors.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

let mockUser: Record<string, unknown> = {};
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser }),
}));

// Keep the network out of the unit test — every roles API call resolves empty.
vi.mock("@/features/roles/api", () => ({
  listUsers: vi.fn(async () => []),
  listRoles: vi.fn(async () => []),
  listPermissions: vi.fn(async () => []),
  listTemplates: vi.fn(async () => []),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  inviteUser: vi.fn(),
  updateUser: vi.fn(),
  blockUser: vi.fn(),
  archiveUser: vi.fn(),
  createAssignment: vi.fn(),
  revokeAssignment: vi.fn(),
}));

import { RolesPage } from "@/features/roles/RolesPage";
import { UsersPage } from "@/features/roles/UsersPage";

function renderPage(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

const SELLER = { home_tenant_id: "t-1", full_name: "Seller", permissions: ["pos.sell"] };
const OWNER = {
  home_tenant_id: "t-1",
  full_name: "Owner",
  permissions: ["users.view", "roles.assign"],
};
// An owner who can also build roles (holds roles.create).
const OWNER_BUILDER = {
  home_tenant_id: "t-1",
  full_name: "Owner",
  permissions: ["users.view", "roles.assign", "roles.create"],
};

describe("UsersPage — route access by users.view", () => {
  beforeEach(() => {
    mockUser = {};
  });
  afterEach(() => vi.clearAllMocks());

  it("blocks a seller reaching /users directly (friendly stub, no table)", () => {
    mockUser = SELLER;
    renderPage(<UsersPage />);
    expect(screen.getByText(/Управление сотрудниками доступно/)).toBeInTheDocument();
    expect(screen.queryByText("+ Пригласить")).not.toBeInTheDocument();
  });

  it("lets an owner (users.view) onto /users", async () => {
    mockUser = OWNER;
    renderPage(<UsersPage />);
    expect(await screen.findByText("+ Пригласить")).toBeInTheDocument();
    expect(screen.queryByText(/Управление сотрудниками доступно/)).not.toBeInTheDocument();
  });
});

describe("RolesPage — route access by users.view", () => {
  beforeEach(() => {
    mockUser = {};
  });
  afterEach(() => vi.clearAllMocks());

  it("blocks a seller reaching /roles directly (friendly stub)", () => {
    mockUser = SELLER;
    renderPage(<RolesPage />);
    expect(screen.getByText(/Управление ролями доступно/)).toBeInTheDocument();
    expect(screen.queryByText("Роли аптеки")).not.toBeInTheDocument();
  });

  it("lets an owner (users.view) onto /roles but hides the builder without roles.create", async () => {
    mockUser = OWNER;
    renderPage(<RolesPage />);
    expect(await screen.findByText("Роли аптеки")).toBeInTheDocument();
    expect(screen.queryByText(/Управление ролями доступно/)).not.toBeInTheDocument();
    // No roles.create → no builder entry point.
    expect(screen.queryByText("+ Создать роль")).not.toBeInTheDocument();
  });

  it("shows the builder («Создать роль») to a user with roles.create", async () => {
    mockUser = OWNER_BUILDER;
    renderPage(<RolesPage />);
    expect(await screen.findByText("+ Создать роль")).toBeInTheDocument();
  });
});
