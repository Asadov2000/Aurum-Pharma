import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaymentEntryForm as PaymentPanel } from "@/features/pos/PaymentEntryForm";
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
  it("fills cash shortcuts without starting payment and retains the insufficient-cash guard", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} mixedPaymentEnabled={false} onPayTile={onPayTile} />);
    const received = screen.getByRole("textbox", { name: "Получено наличными" });

    for (const amount of [10, 50]) {
      fireEvent.click(screen.getByRole("button", { name: String(amount), exact: true }));
      expect(received).toHaveValue(`${amount},00`);
      expect(onPayTile).not.toHaveBeenCalled();
      const accept = screen.getByRole("button", { name: "Принять наличные" });
      if (amount < 59) expect(accept).toBeDisabled();
      else expect(accept).toBeEnabled();
    }
    fireEvent.click(screen.getByRole("button", { name: "Без сдачи" }));
    expect(received).toHaveValue("59,00");
    expect(onPayTile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenCalledWith("cash", "59.00", { cash_received: "59.00" });
  });

  it("opens the numeric keypad explicitly and blocks shortcuts with the cash field", async () => {
    const { rerender } = render(<PaymentPanel {...baseProps} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    rerender(<PaymentPanel {...baseProps} touch />);
    fireEvent.click(
      screen.getByRole("button", { name: "Ввести полученную сумму на экранной клавиатуре" }),
    );
    const keypad = await screen.findByRole("dialog", { name: "Получено наличными" });
    fireEvent.click(within(keypad).getByRole("button", { name: "7", exact: true }));
    fireEvent.click(within(keypad).getByRole("button", { name: "ОК" }));
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("7");
    rerender(<PaymentPanel {...baseProps} touch interactionBlocked />);
    expect(screen.getByRole("button", { name: "Без сдачи" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "50", exact: true })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Ввести полученную сумму на экранной клавиатуре" }),
    ).toBeDisabled();
  });

  it("fills exact cash after a noncash payment without duplicating recorded cash", () => {
    const onPayTile = vi.fn();
    const payments = [
      {
        id: "card",
        sale_id: "s",
        operation_id: null,
        payment_method: "card" as const,
        amount: "20.00",
        currency: "TJS",
      },
      {
        id: "cash",
        sale_id: "s",
        operation_id: null,
        payment_method: "cash" as const,
        amount: "10.00",
        currency: "TJS",
      },
    ];
    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={30}
        remaining={29}
        payments={payments}
        onPayTile={onPayTile}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Без сдачи" }));
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("39,00");
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenCalledWith("cash", "29.00", { cash_received: "29.00" });
  });

  it("uses received cash for the applied amount and shows change", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    const received = screen.getByRole("textbox", { name: "Получено наличными" });
    fireEvent.change(received, { target: { value: "100" } });

    expect(screen.getByText("41,00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));

    expect(onPayTile).toHaveBeenCalledWith("cash", "59.00", {
      cash_received: "100.00",
    });
  });

  it("waits for recorded cash before requesting the mixed remainder by card", () => {
    const onPayTile = vi.fn();
    const { rerender } = render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });

    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenLastCalledWith("cash", "20.00", {
      cash_received: "20.00",
    });
    expect(
      screen.queryByRole("button", { name: "Перейти к оплате картой" }),
    ).not.toBeInTheDocument();

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

    expect(onPayTile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card", "39.00");
  });

  it("separates payment-method selection from starting an external payment", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);

    const card = screen.getByRole("button", { name: "Карта" });
    expect(card.matches(".pos-tile:not(:disabled)")).toBe(true);
    card.focus();
    expect(card).toHaveFocus();
    fireEvent.click(card);
    expect(card).toHaveAttribute("aria-pressed", "true");
    expect(onPayTile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenCalledWith("card", "59.00");
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
    expect(screen.queryByRole("button", { name: "Завершить продажу" })).not.toBeInTheDocument();
    expect(screen.getByText("Сначала примите оставшиеся 59,00 TJS.")).toBeInTheDocument();

    rerender(<PaymentPanel {...baseProps} totalPaid={59} remaining={0} />);
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeEnabled();
  });

  it("shows only configured methods and selects the first available method", () => {
    render(<PaymentPanel {...baseProps} paymentMethods={["qr"]} mixedPaymentEnabled={false} />);

    expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Карта" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "QR" })).toHaveAttribute("aria-pressed", "true");
  });

  it("does not stage a partial amount when mixed payment is disabled", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} mixedPaymentEnabled={false} onPayTile={onPayTile} />);

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
    render(<PaymentPanel {...baseProps} paymentMethods={[]} paymentSettingsUnavailable />);

    expect(screen.getByRole("alert")).toHaveTextContent("Способы оплаты временно недоступны");
    expect(screen.getByText(/Новые платежи заблокированы/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Наличные" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Принять наличные" })).not.toBeInTheDocument();
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

    render(<PaymentPanel {...baseProps} totalPaid={59} remaining={0} payments={[cashPayment]} />);

    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("100,00");
    expect(screen.getByText("41,00")).toBeInTheDocument();
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

    const reset = screen.getByRole("button", { name: "Удалить введённую оплату" });
    expect(reset).toHaveAttribute("title", expect.stringMatching(/банковского терминала/i));
    expect(screen.getByText(/Товары останутся в чеке/i)).toBeInTheDocument();
  });

  it("blocks completion for an overpaid or unconfirmed calculation", () => {
    const { rerender } = render(<PaymentPanel {...baseProps} totalPaid={60} remaining={-1} />);

    expect(screen.getByText("Переплата")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();

    rerender(<PaymentPanel {...baseProps} totalPaid={59} remaining={0} completionBlocked />);
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeDisabled();
    expect(screen.getByText(/Дождитесь завершения текущей операции оплаты/i)).toBeInTheDocument();
  });

  it("offers four choices and retains permitted direct partial cash payments", () => {
    const onPayTile = vi.fn();
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);
    expect(
      within(screen.getByRole("group", { name: "Способ оплаты" })).getAllByRole("button"),
    ).toHaveLength(4);
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenCalledWith("cash", "20.00", { cash_received: "20.00" });
  });

  it("submits a mixed cash part once until owner payments acknowledge it", () => {
    const onPayTile = vi.fn().mockReturnValue(true);
    const p = { ...baseProps, onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "20" },
    });
    const accept = screen.getByRole("button", { name: "Принять наличные" });
    fireEvent.click(accept);
    fireEvent.click(accept);
    expect(onPayTile).toHaveBeenCalledTimes(1);
    view.rerender(<PaymentPanel {...p} touch />);
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenCalledTimes(1);
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={20}
        remaining={39}
        payments={[
          {
            id: "accepted-cash",
            sale_id: "sale",
            operation_id: null,
            payment_method: "cash",
            amount: "20.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    expect(onPayTile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card", "39.00");
  });

  it("allows retry after a definitively rejected synchronous cash part", () => {
    const onPayTile = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    render(<PaymentPanel {...baseProps} onPayTile={onPayTile} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    const accept = screen.getByRole("button", { name: "Принять наличные" });
    fireEvent.click(accept);
    fireEvent.click(accept);
    fireEvent.click(accept);
    expect(onPayTile).toHaveBeenCalledTimes(2);
  });

  it("cannot restart the first part by leaving and returning to mixed payment", () => {
    const onPayTile = vi.fn().mockReturnValue(true);
    const p = { ...baseProps, onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={20}
        remaining={39}
        payments={[
          {
            id: "accepted-cash",
            sale_id: "sale",
            operation_id: null,
            payment_method: "cash",
            amount: "20.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    const methods = within(screen.getByRole("group", { name: "Способ оплаты" }));
    expect(methods.getByRole("button", { name: "Карта" })).toBeDisabled();
    expect(methods.getByRole("button", { name: "Смешанная" })).toBeDisabled();
    fireEvent.click(methods.getByRole("button", { name: "Карта" }));
    fireEvent.click(methods.getByRole("button", { name: "Смешанная" }));
    expect(screen.queryByRole("button", { name: "Принять наличные" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card", "39.00");
    expect(onPayTile).toHaveBeenCalledTimes(2);
  });

  it("resets a split with an existing baseline only after owner props confirm clear", () => {
    const before: Payment = {
      id: "before",
      sale_id: "sale",
      operation_id: null,
      payment_method: "cash",
      amount: "10.00",
      currency: "TJS",
    };
    const accepted: Payment = { ...before, id: "accepted", amount: "20.00" };
    const onClearPayments = vi.fn();
    const p = { ...baseProps, onClearPayments, onPayTile: vi.fn().mockReturnValue(true) };
    const view = render(<PaymentPanel {...p} payments={[before]} totalPaid={10} remaining={49} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    view.rerender(
      <PaymentPanel {...p} payments={[before, accepted]} totalPaid={30} remaining={29} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить введённую оплату" }));
    expect(onClearPayments).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Сумма первой части, TJS")).toHaveValue("20");
    expect(screen.getByLabelText("Сумма первой части, TJS")).toBeDisabled();
    view.rerender(<PaymentPanel {...p} payments={[]} totalPaid={0} remaining={59} />);
    expect(screen.getByLabelText("Сумма первой части, TJS")).toHaveValue("");
    expect(screen.getByLabelText("Сумма первой части, TJS")).toBeEnabled();
    expect(screen.queryByText(/Сумма первой части изменилась/)).not.toBeInTheDocument();
  });

  it("preserves the permitted card and QR pair without requiring cash", () => {
    const onPayTile = vi.fn();
    const p = { ...baseProps, paymentMethods: ["card", "qr"] as PaymentMethod[], onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    expect(screen.queryByRole("textbox", { name: "Получено наличными" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    expect(onPayTile).toHaveBeenLastCalledWith("card", "20.00");
    expect(
      screen.queryByRole("button", { name: "Перейти к оплате по QR" }),
    ).not.toBeInTheDocument();
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={20}
        remaining={39}
        payments={[
          {
            id: "first-card",
            sale_id: "sale",
            operation_id: null,
            payment_method: "card",
            amount: "20.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    expect(onPayTile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате по QR" }));
    expect(onPayTile).toHaveBeenLastCalledWith("qr", "39.00");
  });

  it("allows cash as the second part and preserves tender and change", () => {
    const onPayTile = vi.fn();
    const p = { ...baseProps, onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Первая часть оплаты" })).getByRole("button", {
        name: "Карта",
      }),
    );
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={20}
        remaining={39}
        payments={[
          {
            id: "first-card",
            sale_id: "sale",
            operation_id: null,
            payment_method: "card",
            amount: "20.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "50" },
    });
    expect(screen.getByText("11,00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenLastCalledWith("cash", "39.00", { cash_received: "50.00" });
  });

  it("blocks the second part when the recorded first amount differs", () => {
    const p = { ...baseProps, onPayTile: vi.fn() };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={18}
        remaining={41}
        payments={[
          {
            id: "unexpected",
            sale_id: "sale",
            operation_id: null,
            payment_method: "cash",
            amount: "18.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    expect(screen.getByText(/Сумма первой части изменилась/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Принять наличные" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Перейти к оплате картой" }),
    ).not.toBeInTheDocument();
    expect(p.onPayTile).not.toHaveBeenCalled();
  });

  it("preserves cash entered before confirming the first electronic part", () => {
    const onPayTile = vi.fn();
    const p = { ...baseProps, totalDue: 50, remaining: 50, onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Первая часть оплаты" })).getByRole("button", {
        name: "Карта",
      }),
    );
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Перейти к оплате картой" }));
    view.rerender(
      <PaymentPanel
        {...p}
        totalPaid={20}
        remaining={30}
        payments={[
          {
            id: "first-card",
            sale_id: "sale",
            operation_id: null,
            payment_method: "card",
            amount: "20.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("100");
    expect(screen.getByText("70,00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenLastCalledWith("cash", "30.00", { cash_received: "100.00" });
  });

  it("does not count previously accepted cash again in a new split", () => {
    const onPayTile = vi.fn();
    render(
      <PaymentPanel
        {...baseProps}
        totalPaid={10}
        remaining={49}
        onPayTile={onPayTile}
        payments={[
          {
            id: "cash-before",
            sale_id: "sale",
            operation_id: null,
            payment_method: "cash",
            amount: "10.00",
            currency: "TJS",
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Смешанная" }));
    fireEvent.change(screen.getByLabelText("Сумма первой части, TJS"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Без сдачи" }));
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("30,00");
    expect(screen.getByText("0,00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onPayTile).toHaveBeenCalledWith("cash", "20.00", { cash_received: "20.00" });
  });

  it("keeps recovery available after its method disappears from settings", () => {
    const onRetryPendingPayment = vi.fn(),
      onPayTile = vi.fn();
    render(
      <PaymentPanel
        {...baseProps}
        paymentMethods={["qr"]}
        pendingPaymentMethod="card"
        interactionBlocked
        onRetryPendingPayment={onRetryPendingPayment}
        onPayTile={onPayTile}
      />,
    );
    expect(screen.getByRole("button", { name: "QR", exact: true })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённую оплату" }));
    expect(onRetryPendingPayment).toHaveBeenCalledTimes(1);
    expect(onPayTile).not.toHaveBeenCalled();
  });

  it("retains typed money across unrelated renders and requires explicit completion", () => {
    const onComplete = vi.fn(),
      onPayTile = vi.fn();
    const p = { ...baseProps, onComplete, onPayTile };
    const view = render(<PaymentPanel {...p} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Получено наличными" }), {
      target: { value: "100" },
    });
    view.rerender(<PaymentPanel {...p} paymentMethods={["cash", "card", "qr"]} />);
    expect(screen.getByRole("textbox", { name: "Получено наличными" })).toHaveValue("100");
    fireEvent.click(screen.getByRole("button", { name: "Принять наличные" }));
    expect(onComplete).not.toHaveBeenCalled();
    view.rerender(<PaymentPanel {...p} totalPaid={59} remaining={0} />);
    expect(onComplete).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Принять наличные" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
