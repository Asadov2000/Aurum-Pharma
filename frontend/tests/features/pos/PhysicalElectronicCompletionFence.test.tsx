import { webcrypto } from "node:crypto";
import { api } from "@/lib/api";
import { attempt } from "./unitElectronicCheckoutFixture";
import { installElectronicLocks } from "./unitElectronicCheckoutFixture";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PhysicalPaymentPreparationPanel } from "@/features/pos/PhysicalPaymentPreparationPanel";
import {
  loadUnitPaymentMarker,
  storeUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";
import { authenticatePos } from "./posIdentityFixture";
import { bound, id, quote, saleId, saved, scope } from "./unitPaymentPreparationFixture";
import { result } from "./unitElectronicCheckoutFixture";
const network = vi.hoisted(() => ({ active: vi.fn(), get: vi.fn(), binding: vi.fn() }));
vi.mock("@/features/pos/unitPaymentPreparation", async (original) => ({
  ...(await original<typeof import("@/features/pos/unitPaymentPreparation")>()),
  getActiveUnitPayment: network.active,
  getUnitPaymentIntent: network.get,
  unitPaymentBindingAction: network.binding,
}));
vi.mock("@/features/pos/PhysicalElectronicCheckoutPanel", () => ({
  PhysicalElectronicCheckoutPanel: (props: {
    onCompleted: (doc: ReturnType<typeof result>) => Promise<boolean>;
  }) => (
    <button onClick={() => void props.onCompleted(result()).catch(() => undefined)}>
      Accept deferred saved result
    </button>
  ),
}));
beforeEach(() => {
  installElectronicLocks();
  localStorage.clear();
  authenticatePos(scope);
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(api, "get").mockImplementation(async (url) => ({
    data: String(url).endsWith("/checkout") ? result() : [attempt("consumed")],
  }));
  network.active.mockResolvedValue({ intent: saved, binding: bound });
  network.get.mockResolvedValue(saved);
  network.binding.mockResolvedValue(bound);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("does not clear either sale marker when canonical receipt acceptance finishes after sale/register changed", async () => {
  let resolve: (value: boolean) => void = () => undefined;
  const deferred = new Promise<boolean>((done) => {
    resolve = done;
  });
  const accept = vi.fn().mockReturnValue(deferred);
  storeUnitPaymentMarker(saleId, scope, saved.operation_id);
  const props = {
    saleId,
    scope,
    online: true,
    canPrepare: true,
    canCancel: true,
    physicalOnly: true,
    branchId: quote.branch_id,
    registerId: quote.register_id,
    paymentMethods: ["card" as const],
    mixedPaymentEnabled: true,
    sessionIsActive: () => true,
    onBlockedChange: vi.fn(),
    onElectronicCompleted: accept,
  };
  const view = render(<PhysicalPaymentPreparationPanel {...props} />);
  fireEvent.click(await screen.findByText("Accept deferred saved result"));
  await waitFor(() => expect(accept).toHaveBeenCalledTimes(1));
  const newSale = id(80),
    newOperation = id(81);
  storeUnitPaymentMarker(newSale, scope, newOperation);
  network.active.mockResolvedValue({ intent: null, binding: null });
  network.get.mockResolvedValue(null);
  view.rerender(
    <PhysicalPaymentPreparationPanel {...props} saleId={newSale} registerId={id(82)} />,
  );
  await act(async () => {
    resolve(true);
    await deferred;
  });
  expect(loadUnitPaymentMarker(saleId, scope)).toBe(saved.operation_id);
  expect(loadUnitPaymentMarker(newSale, scope)).toBe(newOperation);
  expect(screen.queryByText("Accept deferred saved result")).toBeNull();
});
