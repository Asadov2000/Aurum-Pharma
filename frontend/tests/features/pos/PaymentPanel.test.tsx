import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaymentPanel } from "@/features/pos/PaymentPanel";
import { type Payment, type PaymentMethod } from "@/features/pos/types";

const baseProps = {
  totalDue: 59,
  totalPaid: 0,
  remaining: 59,
  currency: "TJS",
  payments: [] as Payment[],
  isDraft: true,
  completing: false,
  completionUncertain: false,
  payingMethod: null,
  pendingPaymentMethod: null,
  paymentMethods: ["cash", "card", "qr"] as PaymentMethod[],
  mixedPaymentEnabled: true,
  paymentSettingsLoading: false,
  onPayTile: vi.fn(),
  onComplete: vi.fn(),
  completedReceiptNumber: null,
};

describe("PaymentPanel", () => {
  it("uses received cash for the applied amount and shows change", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    const received = screen.getByRole("textbox", { name: "Получено наличными" });
    fireEvent.change(received, { target: { value: "100" } });

    expect(screen.getByText("41.00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Наличные" }));

    expect(onPayTile).toHaveBeenCalledWith("cash", "59.00");
  });

  it("allows a partial cash amount before paying the remainder by card", () => {
    const onPayTile = vi.fn();
    const { rerender } = render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Наличные" }));
    expect(onPayTile).toHaveBeenLastCalledWith("cash", "20.00");

    const cashPayment: Payment = {
      id: "staged-cash",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "cash",
      amount: "20.00",
      currency: "TJS",
    };
    rerender(
      <PaymentPanel
        {...baseProps}
        totalPaid={20}
        remaining={39}
        payments={[cashPayment]}
        onPayTile={onPayTile}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Карта" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card", "39.00");
  });

  it("does not apply the same received cash twice", () => {
    const onPayTile = vi.fn();
    const cashPayment: Payment = {
      id: "staged-cash",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "cash",
      amount: "20.00",
      currency: "TJS",
    };

    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={20}
        remaining={39}
        payments={[cashPayment]}
        onPayTile={onPayTile}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Наличные" }));

    expect(onPayTile).not.toHaveBeenCalled();
  });

  it("enables completion only after the receipt is fully paid", () => {
    const { rerender } = render(<PaymentPanel {...baseProps} />);
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();

    rerender(<PaymentPanel {...baseProps} totalPaid={59} remaining={0} />);
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeEnabled();
  });

  it("shows only configured methods and selects the first available method", () => {
    render(
      <PaymentPanel
        {...baseProps}
        paymentMethods={["qr"]}
        mixedPaymentEnabled={false}
      />,
    );

    expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Карта" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "QR-код" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not stage a partial amount when mixed payment is disabled", () => {
    const onPayTile = vi.fn();
    render(
      <PaymentPanel
        {...baseProps}
        mixedPaymentEnabled={false}
        onPayTile={onPayTile}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Наличные" }));

    expect(onPayTile).not.toHaveBeenCalled();
    expect(screen.getByText(/Весь чек оплачивается одним способом/i)).toBeInTheDocument();
  });

  it("keeps legacy bank transfers readable in a restored receipt", () => {
    const legacyPayment: Payment = {
      id: "legacy-payment",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "bank_transfer",
      amount: "59.00",
      currency: "TJS",
    };

    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={59}
        remaining={0}
        payments={[legacyPayment]}
        paymentMethods={["qr"]}
      />,
    );

    expect(screen.getByText("Банковский перевод")).toBeInTheDocument();
  });

  it("disables payment choices until server settings finish loading", () => {
    render(<PaymentPanel {...baseProps} paymentSettingsLoading />);

    expect(screen.getByRole("status")).toHaveTextContent("Загрузка способов оплаты");
    expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
  });
});
