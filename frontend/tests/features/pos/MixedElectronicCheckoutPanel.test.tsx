import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { PhysicalPaymentPreparationPanel } from "@/features/pos/PhysicalPaymentPreparationPanel";
import {
  loadUnitPaymentMarker,
  storeUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";
import { authenticatePos } from "./posIdentityFixture";
import { installElectronicLocks } from "./unitElectronicCheckoutFixture";
import { mixedAttempts, mixedContext, mixedResult } from "./mixedElectronicCheckoutFixture";
import { id, scope } from "./unitPaymentPreparationFixture";

const notFound = () => ({ isAxiosError: true, response: { status: 404 } });
beforeEach(() => {
  installElectronicLocks();
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  authenticatePos(scope);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(completed = false) {
  const c = mixedContext("combined");
  storeUnitPaymentMarker(c.intent.plan.request.sale_id, scope, c.intent.operation_id);
  const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/active-binding"))
      return {
        data: { sale_id: c.intent.plan.request.sale_id, intent: c.intent, binding: c.binding },
      };
    if (path.endsWith("/binding")) return { data: c.binding };
    if (path.endsWith(`/intents/${c.intent.operation_id}`)) return { data: c.intent };
    if (path.endsWith("/mixed-checkout/attempts"))
      return { data: mixedAttempts(c, completed ? "consumed" : "pending") };
    if (path.endsWith("/mixed-checkout/checkout") && completed) return { data: mixedResult(c) };
    throw notFound();
  });
  const props = {
    saleId: c.intent.plan.request.sale_id,
    branchId: c.branchId,
    registerId: c.registerId,
    physicalOnly: false,
    scope,
    online: true,
    canPrepare: true,
    canCancel: true,
    paymentMethods: ["cash", "card", "qr"] as ("cash" | "card" | "qr")[],
    mixedPaymentEnabled: true,
    sessionIsActive: () => true,
    onBlockedChange: vi.fn(),
    onElectronicCompleted: vi.fn().mockResolvedValue(true),
  };
  return { c, get, props };
}

describe("mixed electronic preparation and cashier panel", () => {
  it("renders mixed attempts without POST and blocks cancellation while financial attempts remain", async () => {
    const { props, get } = setup(),
      post = vi.spyOn(api, "post");
    render(<PhysicalPaymentPreparationPanel {...props} />);
    await screen.findByRole("button", { name: "Начать явную сверку card" });
    expect(screen.getByRole("button", { name: "Отменить подготовку" })).toBeDisabled();
    expect(
      get.mock.calls.some((call) => String(call[0]).endsWith("/mixed-checkout/attempts")),
    ).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });
  it("recovers the saved mixed receipt in readonly and accepts it with GET-only marker cleanup", async () => {
    const { props, c } = setup(true),
      post = vi.spyOn(api, "post");
    render(<PhysicalPaymentPreparationPanel {...props} canPrepare={false} canCancel={false} />);
    await screen.findByText("Сохранённый чек 001: 2.01 TJS");
    expect(props.onElectronicCompleted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Принять сохранённый чек" }));
    await waitFor(() => expect(loadUnitPaymentMarker(props.saleId, scope)).toBeNull());
    expect(props.onElectronicCompleted).toHaveBeenCalledWith(mixedResult(c));
    expect(post).not.toHaveBeenCalled();
  });
  it("does not accept an obsolete mixed receipt after register switching during GET", async () => {
    const { props, get } = setup(true);
    let resolve: (value: { data: unknown }) => void = () => undefined;
    const delayed = new Promise<{ data: unknown }>((done) => {
      resolve = done;
    });
    const original = get.getMockImplementation()!;
    get.mockImplementation(async (url, config) =>
      String(url).endsWith("/mixed-checkout/checkout") ? delayed : original(url, config),
    );
    const view = render(<PhysicalPaymentPreparationPanel {...props} />);
    await waitFor(() =>
      expect(
        get.mock.calls.some((call) => String(call[0]).endsWith("/mixed-checkout/checkout")),
      ).toBe(true),
    );
    view.rerender(<PhysicalPaymentPreparationPanel {...props} registerId={id(99)} />);
    await act(async () => {
      resolve({ data: mixedResult(mixedContext("combined")) });
      await delayed;
    });
    expect(screen.queryByText("Сохранённый чек 001: 2.01 TJS")).toBeNull();
    expect(props.onElectronicCompleted).not.toHaveBeenCalled();
    expect(loadUnitPaymentMarker(props.saleId, scope)).not.toBeNull();
  });
});
