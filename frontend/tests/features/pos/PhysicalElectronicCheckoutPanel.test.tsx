import { storeUnitPaymentMarker } from "@/features/pos/unitPaymentPreparationStorage";
import { installElectronicLocks } from "./unitElectronicCheckoutFixture";
import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PhysicalElectronicCheckoutPanel } from "@/features/pos/PhysicalElectronicCheckoutPanel";
import { api } from "@/lib/api";
import { authenticatePos } from "./posIdentityFixture";
import { id, scope } from "./unitPaymentPreparationFixture";
import { context, attempt, result } from "./unitElectronicCheckoutFixture";
const notFound = () => ({ isAxiosError: true, response: { status: 404 } });
const props = () => ({
  context: context(),
  online: true,
  writable: true,
  onBarrierChange: vi.fn(),
  onCompleted: vi.fn().mockResolvedValue(true),
});
beforeEach(() => {
  installElectronicLocks();
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  authenticatePos(scope);
  const c = context();
  storeUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe("physical electronic cashier feature", () => {
  it("mount and retry GET never prepare or reconcile automatically", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValue(notFound()),
      post = vi.spyOn(api, "post");
    render(<PhysicalElectronicCheckoutPanel {...props()} />);
    const prepare = await screen.findByRole("button", { name: "Подготовить полный набор попыток" });
    await waitFor(() => expect(prepare).toBeEnabled());
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Проверить сохранённый результат оплаты" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(4));
    expect(post).not.toHaveBeenCalled();
  });
  it("shows historical completed result after reload and accepts it only explicitly", async () => {
    const post = vi.spyOn(api, "post"),
      owner = props();
    vi.spyOn(api, "get").mockImplementation(async (url) => ({
      data: String(url).endsWith("/checkout") ? result() : [attempt("consumed")],
    }));
    render(<PhysicalElectronicCheckoutPanel {...owner} />);
    await screen.findByText("Сохранённый чек 001: 1.99 TJS");
    expect(owner.onCompleted).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Принять сохранённый чек" }));
    await waitFor(() => expect(owner.onCompleted).toHaveBeenCalledTimes(1));
    expect(post).not.toHaveBeenCalled();
  });
  it("an obsolete delayed GET cannot display a receipt after register change", async () => {
    let resolve: (value: { data: unknown }) => void = () => undefined;
    const deferred = new Promise<{ data: unknown }>((done) => {
      resolve = done;
    });
    const get = vi.spyOn(api, "get").mockReturnValueOnce(deferred);
    const owner = props(),
      view = render(<PhysicalElectronicCheckoutPanel {...owner} />);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    view.rerender(
      <PhysicalElectronicCheckoutPanel
        {...owner}
        context={{ ...owner.context, registerId: id(99) }}
      />,
    );
    await act(async () => {
      resolve({ data: result() });
      await deferred;
    });
    expect(screen.queryByText("Сохранённый чек 001: 1.99 TJS")).toBeNull();
    expect(owner.onCompleted).not.toHaveBeenCalled();
    expect(owner.onBarrierChange).not.toHaveBeenCalledWith(false);
  });
  it("reconciliation, evidence and complete are separate explicit operator actions", async () => {
    let status: ReturnType<typeof attempt>["status"] = "pending";
    const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).endsWith("/checkout")) {
        if (status === "consumed") return { data: result() };
        throw notFound();
      }
      return { data: [attempt(status)] };
    });
    const post = vi.spyOn(api, "post").mockImplementation(async (url) => {
      if (String(url).endsWith("/reconciliation")) status = "requires_reconciliation";
      else if (String(url).endsWith("/confirm")) status = "confirmed";
      else if (String(url).endsWith("/complete")) {
        status = "consumed";
        return { data: result() };
      }
      return { data: attempt(status) };
    });
    render(<PhysicalElectronicCheckoutPanel {...props()} />);
    const reconcile = await screen.findByRole("button", { name: "Начать явную сверку card" });
    await waitFor(() => expect(reconcile).toBeEnabled());
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(reconcile);
    const confirm = await screen.findByRole("button", {
      name: "Подтвердить проверенную оплату card",
    });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Терминал"), { target: { value: "T1" } });
    fireEvent.change(screen.getByLabelText("Внешний номер подтверждения"), {
      target: { value: "R1" },
    });
    fireEvent.click(confirm);
    const complete = screen.getByRole("button", {
      name: "Завершить продажу по сохранённому плану",
    });
    await waitFor(() => expect(complete).toBeEnabled());
    fireEvent.click(complete);
    await screen.findByText("Сохранённый чек 001: 1.99 TJS");
    expect(post.mock.calls.map((call) => call[1])).toEqual([
      {},
      { terminal_id: "T1", external_reference: "R1" },
      {},
    ]);
    expect(get.mock.calls.every((call) => !String(call[0]).includes("quote"))).toBe(true);
  });
  it("completes the saved cash plus QR allocation through the same explicit UI", async () => {
    const { hash } = await import("./unitPaymentPreparationFixture");
    const owner = props(),
      c = owner.context;
    c.intent = structuredClone(c.intent);
    c.intent.plan.request.payments = [
      { method: "cash", amount: "0.99" },
      { method: "qr", amount: "1.00" },
    ];
    c.intent.plan.request.cash_tendered = "1.00";
    c.intent.plan.change_amount = "0.01";
    c.intent.plan.plan_hash = hash({
      kind: "unit_payment_plan_v1",
      tenant_id: scope.tenantId,
      branch_id: c.branchId,
      cashier_id: scope.userId,
      sale_id: c.intent.plan.request.sale_id,
      revision_hash: c.intent.plan.request.expected_revision_hash,
      currency: "TJS",
      payments: c.intent.plan.request.payments,
      cash_tendered: "1.00",
    });
    c.binding = { ...c.binding, plan_hash: c.intent.plan.plan_hash };
    const doc = {
      ...result(),
      payments: [
        { id: c.intent.operation_id, method: "cash", amount: "0.99", currency: "TJS" },
        { id: id(21), method: "qr", amount: "1.00", currency: "TJS" },
      ],
    };
    let completed = false;
    vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).endsWith("/checkout")) {
        if (completed) return { data: doc };
        throw notFound();
      }
      return {
        data: [
          {
            ...attempt(completed ? "consumed" : "confirmed"),
            payment_method: "qr",
            amount: "1.00",
          },
        ],
      };
    });
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      completed = true;
      return { data: doc };
    });
    render(<PhysicalElectronicCheckoutPanel {...owner} />);
    const complete = await screen.findByRole("button", {
      name: "Завершить продажу по сохранённому плану",
    });
    await waitFor(() => expect(complete).toBeEnabled());
    fireEvent.click(complete);
    await screen.findByText("Сохранённый чек 001: 1.99 TJS");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[1]).toEqual({});
    expect(owner.onCompleted).not.toHaveBeenCalled();
  });
  it("a temporary write barrier retains the command but still releases busy for GET recovery", async () => {
    let status: ReturnType<typeof attempt>["status"] = "pending";
    vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).endsWith("/checkout")) throw notFound();
      return { data: [attempt(status)] };
    });
    let resolve: (value: { data: unknown }) => void = () => undefined;
    const response = new Promise<{ data: unknown }>((done) => {
      resolve = done;
    });
    const post = vi.spyOn(api, "post").mockReturnValueOnce(response);
    const owner = props(),
      view = render(<PhysicalElectronicCheckoutPanel {...owner} />);
    const reconcile = await screen.findByRole("button", { name: "Начать явную сверку card" });
    await waitFor(() => expect(reconcile).toBeEnabled());
    fireEvent.click(reconcile);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    view.rerender(<PhysicalElectronicCheckoutPanel {...owner} writable={false} />);
    status = "requires_reconciliation";
    await act(async () => {
      resolve({ data: attempt(status) });
      await response;
    });
    const check = screen.getByRole("button", { name: "Проверить сохранённый результат оплаты" });
    await waitFor(() => expect(check).toBeEnabled());
    view.rerender(<PhysicalElectronicCheckoutPanel {...owner} />);
    fireEvent.click(check);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Подтвердить проверенную оплату card" }),
      ).toBeEnabled(),
    );
    expect(post).toHaveBeenCalledTimes(1);
  });
});

it("explicitly releases a proven duplicate reference and corrects the same attempt UUID", async () => {
  let reference: string | null = null;
  vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (String(url).endsWith("/checkout")) throw notFound();
    return {
      data: [
        {
          ...attempt(reference ? "confirmed" : "requires_reconciliation"),
          ...(reference ? { external_reference: reference } : {}),
        },
      ],
    };
  });
  const post = vi
    .spyOn(api, "post")
    .mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          error: { code: "conflict", message: "Terminal payment reference was already used" },
        },
      },
    })
    .mockImplementationOnce(async (_url, payload: unknown) => {
      reference = (payload as { external_reference: string }).external_reference;
      return { data: { ...attempt("confirmed"), external_reference: reference } };
    });
  render(<PhysicalElectronicCheckoutPanel {...props()} />);
  const confirm = await screen.findByRole("button", {
    name: "Подтвердить проверенную оплату card",
  });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Терминал"), { target: { value: "T1" } });
  fireEvent.change(screen.getByLabelText("Внешний номер подтверждения"), {
    target: { value: "R1" },
  });
  fireEvent.click(confirm);
  const repair = await screen.findByRole("button", { name: "Исправить отклонённые реквизиты" });
  await waitFor(() => expect(repair).toBeEnabled());
  expect(post).toHaveBeenCalledTimes(1);
  fireEvent.click(repair);
  await waitFor(() => expect(screen.getByLabelText("Внешний номер подтверждения")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Внешний номер подтверждения"), {
    target: { value: "CORRECTED" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить проверенную оплату card" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Завершить продажу по сохранённому плану" }),
    ).toBeEnabled(),
  );
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1]?.[0]).toBe(post.mock.calls[0]?.[0]);
  expect(post.mock.calls[1]?.[1]).toEqual({ terminal_id: "T1", external_reference: "CORRECTED" });
});
