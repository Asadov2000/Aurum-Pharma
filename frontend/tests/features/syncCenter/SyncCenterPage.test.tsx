import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SyncCenterPage } from "@/features/syncCenter/SyncCenterPage";
import { type SyncMonitoringOverview } from "@/features/syncCenter/types";

const mocks = vi.hoisted(() => ({
  user: {
    platform_capabilities: ["platform.sync.view"],
  } as { platform_capabilities: string[] },
  refetch: vi.fn(),
  useOverview: vi.fn(),
}));

vi.mock("@/components/AccessDeniedCard", () => ({
  AccessDeniedCard: ({ message }: { message: string }) => <div>{message}</div>,
}));

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mocks.user }),
}));

vi.mock("@/features/auth/filterPreferences", () => ({
  useFilterPreferenceKey: () => "test-platform-sync",
}));

vi.mock("@/features/syncCenter/queries", () => ({
  useSyncMonitoringOverview: mocks.useOverview,
}));

const OVERVIEW: SyncMonitoringOverview = {
  generated_at: "2026-08-12T12:00:00Z",
  summary: {
    total_nodes: 1,
    healthy_nodes: 0,
    delayed_nodes: 0,
    offline_nodes: 0,
    critical_nodes: 1,
    revoked_nodes: 0,
    never_connected_nodes: 0,
    expiring_credentials: 1,
    pending_handovers: 0,
    pending_credential_rotations: 0,
  },
  tenants: [{ tenant_id: "tenant-1", tenant_name: "Аптека Сино", node_count: 1 }],
  items: [
    {
      node_id: "node-1",
      tenant_id: "tenant-1",
      tenant_name: "Аптека Сино",
      branch_id: "branch-1",
      branch_name: "Филиал Рудаки",
      register_id: "register-1",
      register_name: "Касса 01",
      display_name: "Edge Рудаки 01",
      mode: "shadow_readonly",
      node_status: "active",
      health: "critical",
      contact_state: "recent",
      integrity_state: "verified",
      credential_expires_at: "2026-08-12T11:59:00Z",
      last_seen_at: "2026-08-12T11:58:00Z",
      latest_report_at: "2026-08-12T11:57:00Z",
      latest_report_status: "matched",
      source_verified: true,
      writer_epoch: 1,
      current_sequence: 120,
      reported_sequence: 120,
      lag_events: 0,
      lifecycle_version: 1,
      credential_rotation_id: null,
      credential_rotation_status: null,
      credential_rotation_activate_before: null,
      credential_rotation_verified_at: null,
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
};

describe("SyncCenterPage", () => {
  beforeEach(() => {
    mocks.user.platform_capabilities = ["platform.sync.view"];
    mocks.refetch.mockReset();
    mocks.useOverview.mockReset();
    mocks.useOverview.mockReturnValue({
      data: OVERVIEW,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: mocks.refetch,
    });
  });

  it("fails closed without the exact platform capability", () => {
    mocks.user.platform_capabilities = [];

    render(<SyncCenterPage />);

    expect(
      screen.getByText("У вас нет доступа к состоянию синхронизации платформы."),
    ).toBeInTheDocument();
    expect(mocks.useOverview).toHaveBeenCalledWith(expect.any(Object), false);
  });

  it("shows safe monitoring details without management actions", () => {
    render(<SyncCenterPage />);

    expect(screen.getAllByText("Требует вмешательства").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Аптека Сино").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: /отозвать|ключ|переключить/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Подробнее об узле Edge Рудаки 01" }));

    expect(screen.getByRole("dialog", { name: "Узел: Edge Рудаки 01" })).toBeInTheDocument();
    expect(screen.getByText("node-1")).toBeInTheDocument();
    expect(screen.queryByText(/credential|checksum/i)).not.toBeInTheDocument();
  });

  it("sends server filters and supports manual refresh", () => {
    render(<SyncCenterPage />);

    fireEvent.change(screen.getByLabelText("Состояние"), {
      target: { value: "critical" },
    });
    expect(mocks.useOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ health: "critical", limit: 25, offset: 0 }),
      true,
    );

    fireEvent.click(screen.getByRole("button", { name: "Обновить состояние синхронизации" }));
    expect(mocks.refetch).toHaveBeenCalledTimes(1);
  });
});
