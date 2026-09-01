import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReceiptPrintModal } from "@/features/pos/ReceiptPrintModal";
import { type ReceiptData } from "@/features/pos/types";

const markReceiptPrintTested = vi.fn();

const receipt: ReceiptData = {
  sale_id: "11111111-1111-4111-8111-111111111111",
  is_refund: false,
  status: "completed",
  pharmacy_name: "Аптека Сино",
  branch_name: "Центр",
  branch_address: "Душанбе",
  branch_license: "LIC-1",
  receipt_number: "01-01-000101",
  original_receipt_number: null,
  datetime: "2026-08-29T12:00:00+05:00",
  cashier_name: "Кассир",
  items: [],
  discount_total: "0.00",
  total: "6.50",
  currency: "TJS",
  payments: [{ method: "cash", amount: "6.50" }],
  paid_total: "6.50",
  change: "0.00",
};

vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({ user: { is_tenant_owner: true } }),
}));

vi.mock("@/features/onboarding/api", () => ({
  markReceiptPrintTested: () => markReceiptPrintTested(),
}));

vi.mock("@/features/pos/queries", () => ({
  useReceiptQuery: () => ({ data: receipt, isLoading: false, error: null }),
}));

describe("ReceiptPrintModal onboarding tracking", () => {
  beforeEach(() => {
    markReceiptPrintTested.mockReset();
    markReceiptPrintTested.mockResolvedValue(undefined);
    vi.spyOn(window, "print").mockImplementation(() => undefined);
  });

  it("records the owner's print check without blocking the browser print dialog", async () => {
    render(
      <ReceiptPrintModal
        saleId={receipt.sale_id}
        registerId="22222222-2222-4222-8222-222222222222"
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Печать чека" }));

    expect(window.print).toHaveBeenCalledOnce();
    await waitFor(() => expect(markReceiptPrintTested).toHaveBeenCalledOnce());
  });
});
