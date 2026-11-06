import { useEffect } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalPaymentPanels } from "@/features/pos/PhysicalPaymentPanels";
import { id, saleId, scope } from "./unitPaymentPreparationFixture";

type CashProps = Parameters<typeof import("@/features/pos/PhysicalCashPanel").PhysicalCashPanel>[0];
type ElectronicProps = Parameters<
  typeof import("@/features/pos/PhysicalPaymentPreparationPanel").PhysicalPaymentPreparationPanel
>[0];
const owners = vi.hoisted(() => ({
  cashMounted: vi.fn(),
  cashUnmounted: vi.fn(),
  electronicMounted: vi.fn(),
  electronicUnmounted: vi.fn(),
  recovered: null as "card" | "qr" | "mixed" | null,
  cashRecovery: false,
}));
vi.mock("@/features/pos/PhysicalCashPanel", () => ({
  PhysicalCashPanel: function Cash(props: CashProps) {
    const { onBlockedChange, onTotalChange } = props;
    useEffect(() => {
      owners.cashMounted();
      onBlockedChange(owners.cashRecovery);
      onTotalChange?.("10.00");
      return owners.cashUnmounted;
    }, [onBlockedChange, onTotalChange]);
    return (
      <section aria-label="Наличный процесс">
        <input aria-label="Получено" defaultValue="" />
      </section>
    );
  },
}));
vi.mock("@/features/pos/PhysicalPaymentPreparationPanel", () => ({
  PhysicalPaymentPreparationPanel: function Electronic(props: ElectronicProps) {
    const { onBlockedChange, onRecoveryMethodChange } = props;
    useEffect(() => {
      owners.electronicMounted();
      onBlockedChange(owners.recovered !== null);
      onRecoveryMethodChange?.(owners.recovered);
      return owners.electronicUnmounted;
    }, [onBlockedChange, onRecoveryMethodChange]);
    return (
      <section aria-label="Электронный процесс">
        <p>{props.selectedMethod}</p>
        <button
          onClick={() => {
            props.onBlockedChange(true);
            props.onRecoveryMethodChange?.(props.selectedMethod ?? "card");
          }}
        >
          Начать подтверждение
        </button>
        <button onClick={() => props.onBlockedChange(false)}>Разрешить результат</button>
      </section>
    );
  },
}));

function props(): Parameters<typeof PhysicalPaymentPanels>[0] {
  return {
    saleId,
    scope,
    branchId: id(4),
    registerId: id(5),
    items: [],
    online: true,
    canPay: true,
    canPrepare: true,
    canCancel: true,
    paymentMethods: ["cash", "card", "qr"],
    mixedPaymentEnabled: true,
    sessionIsActive: () => true,
    onBlockedChange: vi.fn(),
    onPreparationBlockedChange: vi.fn(),
    onCompleted: vi.fn().mockResolvedValue(true),
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  owners.recovered = null;
  owners.cashRecovery = false;
});
afterEach(cleanup);

describe("unified physical payment panel", () => {
  it("clears the previous total when the current quote is explicitly unavailable", async () => {
    const p = props();
    const view = render(<PhysicalPaymentPanels {...p} displayTotal="12.00" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Карта" })).toBeEnabled());
    expect(screen.getByText("12,00")).toBeVisible();
    view.rerender(<PhysicalPaymentPanels {...p} displayTotal={null} />);
    expect(screen.getByText("—")).toBeVisible();
    expect(screen.queryByText("12,00")).not.toBeInTheDocument();
    expect(screen.queryByText("10,00")).not.toBeInTheDocument();
    view.rerender(<PhysicalPaymentPanels {...p} />);
    expect(screen.getByText("10,00")).toBeVisible();
  });

  it("keeps the recovery total even when the current quote is unavailable", async () => {
    owners.cashRecovery = true;
    render(<PhysicalPaymentPanels {...props()} displayTotal={null} />);
    await waitFor(() => expect(screen.getByText("10,00")).toBeVisible());
    expect(screen.getByRole("button", { name: "Наличные" })).toBeDisabled();
  });

  it("switches visible methods while retaining both owners and entered cash", async () => {
    render(<PhysicalPaymentPanels {...props()} />);
    const methods = screen.getByRole("group", { name: "Способ оплаты" });
    await waitFor(() =>
      expect(within(methods).getByRole("button", { name: "Карта" })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Получено"), { target: { value: "50" } });
    fireEvent.click(within(methods).getByRole("button", { name: "Карта" }));
    expect(screen.queryByRole("region", { name: "Наличный процесс" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Электронный процесс" })).toHaveTextContent("card");
    fireEvent.click(within(methods).getByRole("button", { name: "Наличные" }));
    expect(screen.getByLabelText("Получено")).toHaveValue("50");
    expect(owners.cashMounted).toHaveBeenCalledTimes(1);
    expect(owners.electronicMounted).toHaveBeenCalledTimes(1);
    expect(owners.cashUnmounted).not.toHaveBeenCalled();
    expect(owners.electronicUnmounted).not.toHaveBeenCalled();
  });

  it("keeps a recovered electronic plan visible and blocks method changes until resolved", async () => {
    owners.recovered = "qr";
    const p = props();
    const view = render(<PhysicalPaymentPanels {...p} />);
    const methods = screen.getByRole("group", { name: "Способ оплаты" });
    await waitFor(() =>
      expect(within(methods).getByRole("button", { name: "QR" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(within(methods).getByRole("button", { name: "Наличные" })).toBeDisabled();
    expect(screen.getByRole("region", { name: "Электронный процесс" })).toBeVisible();
    view.rerender(<PhysicalPaymentPanels {...p} paymentMethods={["cash"]} canPrepare={false} />);
    expect(screen.getByRole("region", { name: "Электронный процесс" })).toHaveTextContent("qr");
    expect(screen.getByRole("button", { name: "Наличные" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Разрешить результат" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Наличные" })).toBeEnabled());
    expect(owners.electronicUnmounted).not.toHaveBeenCalled();
  });

  it("locks method switching immediately after a payment starts", async () => {
    const p = props();
    render(<PhysicalPaymentPanels {...p} />);
    const qr = screen.getByRole("button", { name: "QR", exact: true });
    await waitFor(() => expect(qr).toBeEnabled());
    fireEvent.click(qr);
    fireEvent.click(screen.getByRole("button", { name: "Начать подтверждение" }));
    expect(screen.getByRole("button", { name: "Наличные" })).toBeDisabled();
    expect(p.onPreparationBlockedChange).toHaveBeenLastCalledWith(true);
    expect(owners.electronicUnmounted).not.toHaveBeenCalled();
  });

  it("shows only configured methods and retains cash recovery if cash was disabled", async () => {
    owners.cashRecovery = true;
    render(<PhysicalPaymentPanels {...props()} paymentMethods={["qr"]} />);
    const methods = screen.getByRole("group", { name: "Способ оплаты" });
    expect(within(methods).getAllByRole("button")).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Наличный процесс" })).toBeVisible(),
    );
    expect(within(methods).getByRole("button", { name: "QR" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Смешанная" })).not.toBeInTheDocument();
  });
});
