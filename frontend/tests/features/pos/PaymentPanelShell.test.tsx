import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaymentPanel } from "@/features/pos/PaymentPanel";
import { type PaymentEntryFormProps } from "@/features/pos/PaymentEntryForm";

vi.mock("@/features/pos/PaymentEntryForm", () => ({
  PaymentEntryForm: (props: PaymentEntryFormProps) => (
    <section aria-label="Платёжная форма">
      {props.pendingPaymentMethod ?? props.completedReceiptNumber ?? "Загружена"}
    </section>
  ),
}));

function props(): PaymentEntryFormProps {
  return {
    totalDue: 0,
    totalPaid: 0,
    remaining: 0,
    currency: "TJS",
    payments: [],
    isDraft: true,
    completing: false,
    completionUncertain: false,
    payingMethod: null,
    pendingPaymentMethod: null,
    paymentMethods: ["cash", "card", "qr"],
    mixedPaymentEnabled: true,
    paymentSettingsLoading: false,
    paymentSettingsUnavailable: false,
    interactionBlocked: false,
    completionBlocked: false,
    onPayTile: vi.fn(),
    onComplete: vi.fn(),
    completedReceiptNumber: null,
  };
}
afterEach(cleanup);

describe("empty payment shell", () => {
  it("shows the familiar disabled empty workspace without mounting its payment owner", () => {
    const p = props();
    render(<PaymentPanel {...p} />);
    const choices = within(screen.getByRole("group", { name: "Способ оплаты" })).getAllByRole(
      "button",
    );
    expect(choices).toHaveLength(4);
    for (const choice of choices) expect(choice).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();
    expect(screen.queryByRole("region", { name: "Платёжная форма" })).not.toBeInTheDocument();
    expect(p.onPayTile).not.toHaveBeenCalled();
    expect(p.onComplete).not.toHaveBeenCalled();
  });

  it("loads the form as soon as a product has an amount", async () => {
    render(<PaymentPanel {...props()} totalDue={10} remaining={10} />);
    expect(await screen.findByRole("region", { name: "Платёжная форма" })).toBeVisible();
  });

  it.each([
    { pendingPaymentMethod: "card" as const },
    { payingMethod: "qr" as const },
    { completionUncertain: true },
    { completing: true },
    { totalPaid: 10 },
    { isDraft: false, completedReceiptNumber: "saved-receipt" },
  ])("loads an existing financial state even with zero current total: %j", async (state) => {
    render(<PaymentPanel {...props()} {...state} />);
    expect(await screen.findByRole("region", { name: "Платёжная форма" })).toBeVisible();
  });

  it("does not invent available methods when settings failed", () => {
    render(<PaymentPanel {...props()} paymentSettingsUnavailable paymentMethods={[]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Способы оплаты временно недоступны");
    expect(screen.queryByRole("group", { name: "Способ оплаты" })).not.toBeInTheDocument();
  });
});
