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
  paymentSettingsUnavailable: false,
  interactionBlocked: false,
  completionBlocked: false,
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
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));

    expect(onPayTile).toHaveBeenCalledWith("cash", "59.00", {
      cash_received: "100.00",
    });
  });

  it("allows a partial cash amount before paying the remainder by card", () => {
    const onPayTile = vi.fn();
    const { rerender } = render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenLastCalledWith("cash", "20.00", {
      cash_received: "20.00",
    });

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
    expect(onPayTile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card");
  });

  it("separates payment-method selection from starting an external payment", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    fireEvent.click(screen.getByRole("button", { name: "Карта" }));
    expect(onPayTile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenCalledWith("card");
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
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));

    expect(onPayTile).not.toHaveBeenCalled();
  });

  it("enables completion only after the receipt is fully paid", () => {
    const { rerender } = render(<PaymentPanel {...baseProps} />);
    const completion = screen.getByRole("button", { name: "Завершить продажу" });
    expect(completion).toBeDisabled();
    expect(completion).toHaveAttribute("title", "Сначала подтвердите полную оплату");

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
    expect(screen.getByRole("button", { name: "Принять наличные" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));

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
    expect(screen.queryByRole("textbox", { name: "Получено наличными" })).not.toBeInTheDocument();
  });

  it("fails closed when server payment settings are unavailable", () => {
    render(
      <PaymentPanel
        {...baseProps}
        paymentMethods={[]}
        paymentSettingsUnavailable
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Способы оплаты временно недоступны",
    );
    expect(screen.getByText(/Новые платежи заблокированы/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Принять наличные" }),
    ).not.toBeInTheDocument();
  });

  it("restores cash received separately from the amount applied to the sale", () => {
    const cashPayment: Payment & { metadata: { cash_received: string } } = {
      id: "staged-cash",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "cash",
      amount: "59.00",
      currency: "TJS",
      metadata: { cash_received: "100.00" },
    };

    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={59}
        remaining={0}
        payments={[cashPayment]}
      />,
    );

    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("100,00");
    expect(screen.getByText("41.00")).toBeInTheDocument();
  });

  it("labels a local reset without implying a bank or terminal cancellation", () => {
    const payment: Payment = {
      id: "staged-card",
      sale_id: "sale-1",
      operation_id: null,
      payment_method: "card",
      amount: "20.00",
      currency: "TJS",
    };
    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={20}
        remaining={39}
        payments={[payment]}
        onClearPayments={vi.fn()}
      />,
    );

    const reset = screen.getByRole("button", { name: "Сбросить расчёт" });
    expect(reset).toHaveAttribute("title", expect.stringMatching(/внешнего терминала/i));
    expect(screen.queryByRole("button", { name: "Очистить оплату" })).not.toBeInTheDocument();
  });

  it("blocks completion for an overpaid or unconfirmed calculation", () => {
    const { rerender } = render(
      <PaymentPanel {...baseProps} totalPaid={60} remaining={-1} />,
    );

    expect(screen.getByText("Переплата")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();

    rerender(
      <PaymentPanel
        {...baseProps}
        totalPaid={59}
        remaining={0}
        completionBlocked
      />,
    );
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();
  });
});
