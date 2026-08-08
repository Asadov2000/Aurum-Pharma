import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getTenantSettings = vi.fn();
const updateTenantSettings = vi.fn();

vi.mock("@/features/foundation/api", () => ({
  getTenantSettings: (...args: unknown[]) => getTenantSettings(...args),
  updateTenantSettings: (...args: unknown[]) => updateTenantSettings(...args),
}));

import { SettingsPage } from "@/features/foundation/SettingsPage";

const SETTINGS = {
  tenant_id: "tenant-1",
  expiry_thresholds: { yellow: 12, orange: 6, red: 3 },
  expired_sale_mode: "warning" as const,
  refund_reason_mode: "optional" as const,
  session_admin_minutes: 60,
  session_pos_minutes: 480,
  pin_mode_enabled: false,
  draft_sale_lifetime_min: 30,
  prescription_warning_text: "",
  pos_payment_methods: ["cash", "card", "qr"] as const,
  pos_mixed_payment_enabled: true,
  report_timezone: "Asia/Dushanbe",
  updated_at: "2026-07-30T08:00:00Z",
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

describe("SettingsPage POS payment settings", () => {
  beforeEach(() => {
    getTenantSettings.mockReset();
    updateTenantSettings.mockReset();
    getTenantSettings.mockResolvedValue(SETTINGS);
    updateTenantSettings.mockResolvedValue(SETTINGS);
  });

  it("saves enabled methods and the mixed-payment switch", async () => {
    renderPage();

    const cash = await screen.findByRole("checkbox", { name: "Наличные" });
    const card = screen.getByRole("checkbox", { name: "Карта" });
    const qr = screen.getByRole("checkbox", { name: "QR-код" });
    const mixed = screen.getByRole("checkbox", { name: "Разрешить смешанную оплату" });

    expect(cash).toBeChecked();
    expect(card).toBeChecked();
    expect(qr).toBeChecked();

    fireEvent.click(qr);
    fireEvent.click(cash);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: "Карта" })).toBeDisabled();
    });
    fireEvent.click(mixed);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      expect(updateTenantSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          pos_payment_methods: ["card"],
          pos_mixed_payment_enabled: false,
        }),
      );
    });
  });
});
