import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TrainingCheckoutPanel } from "@/features/pos/TrainingCheckoutPanel";
import type { SaleDetails } from "@/features/pos/types";
import { trainingFixture } from "./trainingCheckoutFixture";
const m = vi.hoisted(() => ({
  pending: false,
  inspect: vi.fn(),
  quote: vi.fn(),
  start: vi.fn(),
  retry: vi.fn(),
  ack: vi.fn(),
}));
vi.mock("@/features/pos/trainingCheckoutPresence", () => ({
  hasTrainingCheckout: () => m.pending,
}));
vi.mock("@/features/pos/trainingCheckout", async (original) => ({
  ...(await original<typeof import("@/features/pos/trainingCheckout")>()),
  inspectTrainingCheckout: m.inspect,
  getTrainingQuote: m.quote,
  startTrainingCheckout: m.start,
  retryTrainingCheckout: m.retry,
  acknowledgeTrainingCheckout: m.ack,
}));
function props() {
  const f = trainingFixture();
  return {
    sale: {
      id: f.scope.saleId,
      branch_id: f.scope.branchId,
      is_test: true,
      status: "draft",
    } as SaleDetails,
    scope: { tenantId: f.scope.tenantId, userId: f.scope.userId },
    registerId: f.scope.registerId,
    canStart: true,
    paymentMethods: ["cash", "card", "qr"],
    mixedPaymentEnabled: true,
    sessionIsActive: () => true,
    onBlockedChange: vi.fn(),
    refreshSale: vi.fn().mockResolvedValue({
      id: f.result.sale_id,
      tenant_id: f.result.tenant_id,
      branch_id: f.result.branch_id,
      cashier_user_id: f.result.cashier_id,
      register_id: f.result.register_id,
      shift_id: f.result.shift_id,
      total_amount: f.result.total_amount,
      receipt_number: f.result.receipt_number,
      is_test: true,
      status: "completed",
      payments: [],
    }),
    onCompleted: vi.fn().mockResolvedValue(true),
    onPrint: vi.fn(),
    onNewSale: vi.fn(),
  };
}
beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  m.pending = false;
  m.inspect.mockResolvedValue(null);
  m.quote.mockResolvedValue(trainingFixture().quote);
  m.start.mockImplementation(async () => {
    m.pending = true;
    return trainingFixture().result;
  });
  m.ack.mockResolvedValue(undefined);
});
afterEach(cleanup);
describe("cashier TEST checkout", () => {
  it("calculates and completes only an explicitly simulated training request", async () => {
    const p = props();
    render(<TrainingCheckoutPanel {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Рассчитать учебный чек" }));
    await screen.findByRole("button", { name: "Завершить учебную продажу" });
    fireEvent.change(screen.getByLabelText("Учебно получено наличными, TJS"), {
      target: { value: "1.00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Завершить учебную продажу" }));
    await waitFor(() => expect(p.onCompleted).toHaveBeenCalled());
    expect(m.start.mock.calls[0]?.[1]).toMatchObject({
      schema_version: 1,
      cash_tendered: "1.00",
      simulated_payments: [{ method: "cash", amount: "0.32" }],
    });
    expect(screen.getByText("Учебная сдача: 0.68 TJS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть TEST-чек" }));
    expect(p.onPrint).toHaveBeenCalledOnce();
  });
  it("recovers saved receipt on reload without quote or POST", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "completed", result: trainingFixture().result });
    const p = props();
    p.canStart = false;
    render(<TrainingCheckoutPanel {...p} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Открыть TEST-чек" })).toBeEnabled(),
    );
    expect(m.start).not.toHaveBeenCalled();
    expect(m.quote).not.toHaveBeenCalled();
    expect(p.onBlockedChange).toHaveBeenCalledWith(true);
  });
  it("not_found never automatically retries; explicit retry uses durable command", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "not_found" });
    m.retry.mockResolvedValue(trainingFixture().result);
    render(<TrainingCheckoutPanel {...props()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Повторить учебную команду" }));
    await waitFor(() => expect(m.retry).toHaveBeenCalledOnce());
    expect(m.start).not.toHaveBeenCalled();
  });
  it("lost response keeps recovery visible, without another command", async () => {
    m.start.mockImplementationOnce(async () => {
      m.pending = true;
      throw new Error("lost");
    });
    render(<TrainingCheckoutPanel {...props()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Рассчитать учебный чек" }));
    fireEvent.click(await screen.findByRole("button", { name: "Завершить учебную продажу" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Проверить учебную операцию" })).toBeEnabled();
    expect(m.start).toHaveBeenCalledOnce();
    expect(m.retry).not.toHaveBeenCalled();
  });
  it("waits for parent verification before print or journal acknowledgement", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "completed", result: trainingFixture().result });
    const p = props();
    p.onCompleted.mockResolvedValue(false);
    render(<TrainingCheckoutPanel {...p} />);
    await screen.findByText(/Учебный чек TEST-1/);
    expect(screen.getByRole("button", { name: "Открыть TEST-чек" })).toBeDisabled();
    expect(m.ack).not.toHaveBeenCalled();
  });
  it("new sale requires a fresh explicit acknowledgement", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "completed", result: trainingFixture().result });
    const p = props();
    render(<TrainingCheckoutPanel {...p} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Новая продажа" })).toBeEnabled(),
    );
    expect(m.ack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Новая продажа" }));
    await waitFor(() => expect(p.onNewSale).toHaveBeenCalledOnce());
    expect(m.ack).toHaveBeenCalledOnce();
  });
  it("rejects disabled payment method and does not issue command", async () => {
    const p = props();
    p.paymentMethods = ["cash"];
    render(<TrainingCheckoutPanel {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Рассчитать учебный чек" }));
    await screen.findByLabelText("Учебная карта, TJS");
    fireEvent.change(screen.getByLabelText("Учебные наличные, TJS"), { target: { value: "0.00" } });
    fireEvent.change(screen.getByLabelText("Учебная карта, TJS"), { target: { value: "0.32" } });
    fireEvent.click(screen.getByRole("button", { name: "Завершить учебную продажу" }));
    await screen.findByRole("alert");
    expect(m.start).not.toHaveBeenCalled();
  });
  it("keeps print disabled when refreshed sale is not test-only", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "completed", result: trainingFixture().result });
    const p = props();
    p.refreshSale.mockResolvedValue({ ...(await p.refreshSale()), is_test: false });
    render(<TrainingCheckoutPanel {...p} />);
    await screen.findByRole("alert");
    expect(p.onCompleted).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Открыть TEST-чек" })).toBeDisabled();
  });
  it("discards a refreshed result after register context changes", async () => {
    m.pending = true;
    m.inspect.mockResolvedValue({ state: "completed", result: trainingFixture().result });
    const p = props();
    let resolve: (value: unknown) => void = () => undefined;
    p.refreshSale.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const view = render(<TrainingCheckoutPanel {...p} />);
    await waitFor(() => expect(p.refreshSale).toHaveBeenCalledOnce());
    view.rerender(
      <TrainingCheckoutPanel {...p} registerId="00000000-0000-4000-8000-000000000099" />,
    );
    await act(async () => {
      resolve(trainingFixture().result);
    });
    expect(p.onCompleted).not.toHaveBeenCalled();
  });
  it("does not accept an asynchronous result after unmount", async () => {
    let done: (value: unknown) => void = () => undefined;
    m.pending = true;
    m.inspect.mockReturnValue(
      new Promise((resolve) => {
        done = resolve;
      }),
    );
    const p = props();
    const view = render(<TrainingCheckoutPanel {...p} />);
    view.unmount();
    await act(async () => {
      done({ state: "completed", result: trainingFixture().result });
    });
    expect(p.onCompleted).not.toHaveBeenCalled();
  });
});
