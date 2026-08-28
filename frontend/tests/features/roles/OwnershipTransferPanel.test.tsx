import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listOwnershipTransfers = vi.fn();
const acceptOwnershipTransfer = vi.fn();
const cancelOwnershipTransfer = vi.fn();
const logout = vi.fn();

let mockUser: Record<string, unknown> = {};

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: mockUser, logout }),
}));

vi.mock("@/features/roles/api", () => ({
  listOwnershipTransfers: (...args: unknown[]) => listOwnershipTransfers(...args),
  acceptOwnershipTransfer: (...args: unknown[]) => acceptOwnershipTransfer(...args),
  cancelOwnershipTransfer: (...args: unknown[]) => cancelOwnershipTransfer(...args),
}));

import { OwnershipTransferPanel } from "@/features/roles/OwnershipTransferPanel";

const PENDING_TRANSFER = {
  id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "tenant-1",
  initiator_membership_id: "membership-owner",
  initiator_user_id: "owner-1",
  initiator_full_name: "Текущий владелец",
  target_membership_id: "membership-target",
  target_user_id: "target-1",
  target_full_name: "Будущий владелец",
  status: "pending" as const,
  expires_at: "2026-08-31T10:00:00Z",
  completed_at: null,
  cancelled_at: null,
  created_at: "2026-08-28T10:00:00Z",
  updated_at: "2026-08-28T10:00:00Z",
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OwnershipTransferPanel />
    </QueryClientProvider>,
  );
}

describe("OwnershipTransferPanel", () => {
  beforeEach(() => {
    mockUser = {
      id: "target-1",
      home_tenant_id: "tenant-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      is_administrator: false,
    };
    listOwnershipTransfers.mockReset();
    acceptOwnershipTransfer.mockReset();
    cancelOwnershipTransfer.mockReset();
    logout.mockReset();
    listOwnershipTransfers.mockResolvedValue([PENDING_TRANSFER]);
    acceptOwnershipTransfer.mockResolvedValue({
      transfer: { ...PENDING_TRANSFER, status: "completed" },
      sessions_revoked: true,
    });
    cancelOwnershipTransfer.mockResolvedValue({
      transfer: { ...PENDING_TRANSFER, status: "cancelled" },
      sessions_revoked: false,
    });
    logout.mockResolvedValue(undefined);
  });

  it("requires confirmation, accepts the transfer and clears the revoked session", async () => {
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Принять владение" }));
    expect(screen.getByText("Стать владельцем аптеки?")).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole("button", { name: "Принять владение" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => {
      expect(acceptOwnershipTransfer).toHaveBeenCalledWith(PENDING_TRANSFER.id);
      expect(logout).toHaveBeenCalledTimes(1);
    });
  });

  it("allows only the initiating owner to cancel a pending request", async () => {
    mockUser = {
      id: "owner-1",
      home_tenant_id: "tenant-1",
      active_tenant_id: "tenant-1",
      is_developer: false,
      is_administrator: false,
    };
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Отменить запрос" }));
    expect(screen.getByText("Отменить передачу владения?")).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole("button", { name: "Отменить запрос" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => {
      expect(cancelOwnershipTransfer).toHaveBeenCalledWith(PENDING_TRANSFER.id);
    });
    expect(logout).not.toHaveBeenCalled();
  });
});
