import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SyncNodeActionModal } from "@/features/syncCenter/SyncNodeActionModal";
import { type SyncMonitoringNode } from "@/features/syncCenter/types";

const mocks = vi.hoisted(() => ({
  start: {
    isPending: false,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
  },
  transition: {
    isPending: false,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
  },
  revoke: {
    isPending: false,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
  },
}));

vi.mock("@/features/syncCenter/queries", () => ({
  useStartSyncCredentialRotation: () => mocks.start,
  useTransitionSyncCredentialRotation: () => mocks.transition,
  useRevokeSyncNode: () => mocks.revoke,
}));

const NODE: SyncMonitoringNode = {
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
  health: "healthy",
  contact_state: "recent",
  integrity_state: "verified",
  credential_expires_at: "2026-10-01T00:00:00Z",
  last_seen_at: "2026-08-12T11:58:00Z",
  latest_report_at: "2026-08-12T11:57:00Z",
  latest_report_status: "matched",
  source_verified: true,
  writer_epoch: 1,
  current_sequence: 120,
  reported_sequence: 120,
  lag_events: 0,
  lifecycle_version: 7,
  credential_rotation_id: null,
  credential_rotation_status: null,
  credential_rotation_activate_before: null,
  credential_rotation_verified_at: null,
};

describe("SyncNodeActionModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.start.mutateAsync.mockResolvedValue({
      operation_id: "8c869603-23cb-4586-9db0-ed137a337695",
      node_id: NODE.node_id,
      rotation_id: "rotation-1",
      status: "pending",
      lifecycle_version: 8,
      credential: "node-1.once-only-secret",
      credential_issued_at: "2026-08-12T12:00:00Z",
      credential_expires_at: "2026-11-10T12:00:00Z",
      activate_before: "2026-08-13T12:00:00Z",
      replayed: false,
    });
  });

  it("requires the exact node name and only reveals the new credential once", async () => {
    const onClose = vi.fn();
    const onCompleted = vi.fn();
    render(
      <SyncNodeActionModal
        action="rotate"
        node={NODE}
        onClose={onClose}
        onCompleted={onCompleted}
      />,
    );

    fireEvent.change(screen.getByLabelText("Комментарий"), {
      target: { value: "Плановая безопасная замена ключа" },
    });
    fireEvent.change(screen.getByLabelText(/Для подтверждения введите/), {
      target: { value: "другой узел" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать новый ключ" }));

    expect(await screen.findByText("Имя узла не совпадает")).toBeInTheDocument();
    expect(mocks.start.mutateAsync).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/Для подтверждения введите/), {
      target: { value: NODE.display_name },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать новый ключ" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать новый ключ" }));

    await waitFor(() => expect(mocks.start.mutateAsync).toHaveBeenCalledTimes(1));
    expect(mocks.start.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_version: 7,
        confirmation_name: NODE.display_name,
        credential_valid_days: 90,
      }),
    );
    expect(await screen.findByDisplayValue("node-1.once-only-secret")).toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(onCompleted).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    expect(mocks.start.reset).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
