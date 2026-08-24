import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn();

const SETTINGS = {
  tenant_id: "tenant-1",
  version: 4,
  expiry_thresholds: { yellow: 12, orange: 6, red: 3 },
  expired_sale_mode: "strict" as const,
  refund_reason_mode: "optional" as const,
  session_admin_minutes: 60,
  session_pos_minutes: 480,
  pin_mode_enabled: false,
  draft_sale_lifetime_min: 30,
  prescription_warning_text: "",
  pos_payment_methods: ["cash", "card", "qr"] as const,
  pos_mixed_payment_enabled: true,
  report_timezone: "Asia/Dushanbe" as const,
  updated_at: "2026-07-30T08:00:00Z",
};

vi.mock("@/features/foundation/queries", () => ({
  useTenantSettingsQuery: () => ({
    data: SETTINGS,
    error: null,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useUpdateTenantSettings: () => ({
    mutateAsync: (...args: unknown[]) => mutateAsync(...args),
    isPending: false,
  }),
}));

import { OwnerSettingsPanel } from "@/features/settings/OwnerSettingsPanel";

describe("OwnerSettingsPanel", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue(SETTINGS);
  });

  it("uses optimistic versioning when saving pharmacy payment rules", async () => {
    render(<OwnerSettingsPanel section="sales" />);

    const qr = screen.getByRole("button", { name: "QR-код" });
    expect(qr).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(qr);
    fireEvent.click(screen.getByRole("checkbox", { name: "Разрешить" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          expected_version: 4,
          pos_payment_methods: ["cash", "card"],
          pos_mixed_payment_enabled: false,
        }),
      );
    });
  });

  it("shows expired-medicine blocking as a non-configurable rule", () => {
    render(<OwnerSettingsPanel section="sales" />);

    expect(screen.getByText("Продажа запрещена")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Просроченные препараты/ })).toBeNull();
  });
});
