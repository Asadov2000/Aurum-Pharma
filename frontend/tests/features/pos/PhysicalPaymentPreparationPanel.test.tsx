import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { PhysicalPaymentPreparationPanel } from "@/features/pos/PhysicalPaymentPreparationPanel";
import {
  storeUnitPaymentMarker,
  loadUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";

import { authenticatePos } from "./posIdentityFixture";
import {
  bound,
  cancelled,
  id,
  plan,
  quote,
  request,
  saleId,
  saved,
  scope,
} from "./unitPaymentPreparationFixture";

const network = vi.hoisted(() => ({
  active: vi.fn(),
  get: vi.fn(),
  quote: vi.fn(),
  preview: vi.fn(),
  save: vi.fn(),
  binding: vi.fn(),
}));
vi.mock("@/features/pos/unitPaymentPreparation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/pos/unitPaymentPreparation")>()),
  getActiveUnitPayment: network.active,
  getUnitPaymentIntent: network.get,
  getUnitPaymentQuote: network.quote,
  previewUnitPayment: network.preview,
  saveUnitPaymentIntent: network.save,
  unitPaymentBindingAction: network.binding,
}));
const props = () => ({
  saleId,
  scope,
  online: true,
  canPrepare: true,
  canCancel: true,
  paymentMethods: ["cash", "card", "qr"] as ("cash" | "card" | "qr")[],
  mixedPaymentEnabled: true,
  sessionIsActive: () => true,
  beforeStart: vi.fn().mockResolvedValue(undefined),
  onBlockedChange: vi.fn(),
});
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  authenticatePos(scope);
  vi.stubGlobal("crypto", { randomUUID: () => id(9) });
  network.active.mockResolvedValue({ intent: null, binding: null });
  network.get.mockResolvedValue(null);
  network.quote.mockResolvedValue({ quote, revision_hash: request.expected_revision_hash });
  network.preview.mockResolvedValue(plan);
  network.save.mockResolvedValue(saved);
  network.binding.mockImplementation((_intent, action: string) =>
    Promise.resolve(action === "bind" ? bound : action === "cancel-binding" ? cancelled : null),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function openForm() {
  const open = screen.getByRole("button", { name: "Подготовка карты/QR" });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  await waitFor(() => expect(screen.getByLabelText("Картой, TJS")).toHaveValue("1.99"));
}
async function prepare() {
  await openForm();
  fireEvent.click(screen.getByRole("button", { name: "Подготовить оплату" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Закрепить план" })).toBeEnabled());
}

describe("physical payment preparation", () => {
  it("checks an alternative active binding read-only while retaining an unknown local marker", async () => {
    const { webcrypto } = await import("node:crypto");
    vi.stubGlobal("crypto", webcrypto);
    storeUnitPaymentMarker(saleId, scope, id(90));
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    const get = vi
      .spyOn(api, "get")
      .mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    const post = vi.spyOn(api, "post");
    const p = {
      ...props(),
      physicalOnly: true,
      branchId: quote.branch_id,
      registerId: quote.register_id,
      onElectronicCompleted: vi.fn(),
    };
    render(<PhysicalPaymentPreparationPanel {...p} />);
    const cancel = await screen.findByRole("button", { name: "Отменить подготовку" });
    await waitFor(() => expect(cancel).toBeEnabled());
    expect(get).toHaveBeenCalledTimes(2);
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Подготовить полный набор попыток" })).toBeDisabled();
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(90));
    network.active.mockResolvedValue({ intent: null, binding: null });
    fireEvent.click(cancel);
    await screen.findByText(/Результат сохранения пока неизвестен/);
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(90));
    expect(network.binding).toHaveBeenCalledWith(saved, "cancel-binding", scope);
    expect(p.onElectronicCompleted).not.toHaveBeenCalled();
  });

  it("recovers another tab's binding without discarding an unknown local operation", async () => {
    storeUnitPaymentMarker(saleId, scope, id(90));
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    render(<PhysicalPaymentPreparationPanel {...props()} />);
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(90));
    network.active.mockResolvedValue({ intent: null, binding: null });
    fireEvent.click(screen.getByRole("button", { name: "Отменить подготовку" }));
    await screen.findByText(/Результат сохранения пока неизвестен/);
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(90));
    expect(network.binding).toHaveBeenCalledWith(saved, "cancel-binding", scope);
  });
  it("does not restore an obsolete active snapshot after confirmed cancellation", async () => {
    storeUnitPaymentMarker(saleId, scope, id(9));
    network.active.mockResolvedValueOnce({ intent: saved, binding: bound });
    network.get.mockResolvedValue(saved);
    network.binding.mockResolvedValue(cancelled);
    const p = props();
    render(<PhysicalPaymentPreparationPanel {...p} />);
    await waitFor(() => expect(p.onBlockedChange).toHaveBeenLastCalledWith(false));
    expect(network.active).toHaveBeenCalledTimes(2);
    expect(loadUnitPaymentMarker(saleId, scope)).toBeNull();
    expect(screen.queryByText("План закреплён. Корзина защищена от изменений.")).toBeNull();
  });
  it("recovers a lost binding response after reload without repeating the mutation", async () => {
    const first = render(<PhysicalPaymentPreparationPanel {...props()} />);
    await prepare();
    network.binding.mockRejectedValueOnce(new Error("binding response lost"));
    fireEvent.click(screen.getByRole("button", { name: "Закрепить план" }));
    await screen.findByRole("alert");
    first.unmount();
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    network.get.mockResolvedValue(saved);
    network.binding.mockResolvedValue(bound);
    render(<PhysicalPaymentPreparationPanel {...props()} />);
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    expect(network.binding.mock.calls.filter((call) => call[1] === "bind")).toHaveLength(1);
    expect(network.binding).toHaveBeenLastCalledWith(saved, "binding", scope);
  });
  it("does not persist or submit a plan after the session changes during preview", async () => {
    let resolve: ((value: typeof plan) => void) | undefined;
    network.preview.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    let active = true;
    render(<PhysicalPaymentPreparationPanel {...props()} sessionIsActive={() => active} />);
    await openForm();
    fireEvent.click(screen.getByRole("button", { name: "Подготовить оплату" }));
    await waitFor(() => expect(network.preview).toHaveBeenCalled());
    active = false;
    await act(async () => {
      resolve?.(plan);
    });
    expect(network.save).not.toHaveBeenCalled();
    expect(loadUnitPaymentMarker(saleId, scope)).toBeNull();
  });
  it("previews, saves, binds and finally cancels without accepting money", async () => {
    const p = props();
    render(<PhysicalPaymentPreparationPanel {...p} />);
    await prepare();
    expect(network.preview).toHaveBeenCalledWith(request, scope);
    expect(network.save).toHaveBeenCalledWith(id(9), plan, scope);
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(9));
    const beforeBindCalls = p.beforeStart.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Закрепить план" }));
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    expect(p.onBlockedChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Отменить подготовку" }));
    await waitFor(() => expect(loadUnitPaymentMarker(saleId, scope)).toBeNull());
    await waitFor(() => expect(p.onBlockedChange).toHaveBeenLastCalledWith(false));
    expect(p.beforeStart.mock.calls.length).toBe(beforeBindCalls);
    expect(screen.getByText("Деньги не приняты. Электронная оплата ещё недоступна.")).toBeVisible();
  });
  it("recovers a server binding after reload or another tab without automatic posting", async () => {
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    const p = props();
    render(<PhysicalPaymentPreparationPanel {...p} />);
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(9));
    expect(network.save).not.toHaveBeenCalled();
    expect(network.binding).not.toHaveBeenCalled();
    expect(p.onBlockedChange).toHaveBeenLastCalledWith(true);
  });
  it("keeps an uncertain save UUID across reload and retries exactly that UUID", async () => {
    const first = render(<PhysicalPaymentPreparationPanel {...props()} />);
    network.save.mockRejectedValueOnce(new Error("lost response"));
    await openForm();
    fireEvent.click(screen.getByRole("button", { name: "Подготовить оплату" }));
    await screen.findByRole("alert");
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(9));
    first.unmount();
    vi.stubGlobal("crypto", { randomUUID: () => id(99) });
    render(<PhysicalPaymentPreparationPanel {...props()} />);
    await screen.findByText(/Результат сохранения пока неизвестен/);
    expect(network.save).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Картой, TJS"), { target: { value: "1.99" } });
    fireEvent.click(screen.getByRole("button", { name: "Подготовить оплату" }));
    await waitFor(() => expect(network.save).toHaveBeenCalledTimes(2));
    expect(network.save).toHaveBeenLastCalledWith(id(9), plan, scope);
  });
  it("recovers a committed lost save through GET without resending it", async () => {
    storeUnitPaymentMarker(saleId, scope, id(9));
    network.get.mockResolvedValue(saved);
    render(<PhysicalPaymentPreparationPanel {...props()} />);
    await screen.findByRole("button", { name: "Закрепить план" });
    expect(network.save).not.toHaveBeenCalled();
    expect(network.preview).not.toHaveBeenCalled();
  });
  it("keeps the marker after a lost cancel response and clears only confirmed cancellation", async () => {
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    render(<PhysicalPaymentPreparationPanel {...props()} />);
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    network.binding.mockRejectedValueOnce(new Error("cancel response lost"));
    fireEvent.click(screen.getByRole("button", { name: "Отменить подготовку" }));
    await screen.findByRole("alert");
    expect(loadUnitPaymentMarker(saleId, scope)).toBe(id(9));
    network.active.mockResolvedValue({ intent: null, binding: null });
    network.get.mockResolvedValue(saved);
    network.binding.mockResolvedValue(cancelled);
    fireEvent.click(screen.getByRole("button", { name: "Проверить результат подготовки" }));
    await waitFor(() => expect(loadUnitPaymentMarker(saleId, scope)).toBeNull());
    expect(network.binding).toHaveBeenLastCalledWith(saved, "binding", scope);
  });
  it("allows readonly recovery while all mutation buttons remain disabled", async () => {
    network.active.mockResolvedValue({ intent: saved, binding: bound });
    render(<PhysicalPaymentPreparationPanel {...props()} canPrepare={false} canCancel={false} />);
    await screen.findByText("План закреплён. Корзина защищена от изменений.");
    expect(screen.getByRole("button", { name: "Отменить подготовку" })).toBeDisabled();
    expect(network.save).not.toHaveBeenCalled();
    expect(network.binding).not.toHaveBeenCalled();
  });
  it("fails closed when the initial server state is unavailable", async () => {
    network.active.mockRejectedValue(new Error("server unavailable"));
    const p = props();
    render(<PhysicalPaymentPreparationPanel {...p} />);
    await screen.findByRole("alert");
    expect(p.onBlockedChange).toHaveBeenLastCalledWith(true);
    expect(network.save).not.toHaveBeenCalled();
  });
  it("rejects unavailable mixed methods before preview and never persists a marker", async () => {
    render(<PhysicalPaymentPreparationPanel {...props()} mixedPaymentEnabled={false} />);
    await openForm();
    fireEvent.change(screen.getByLabelText("Картой, TJS"), { target: { value: "1.00" } });
    fireEvent.change(screen.getByLabelText("Через QR, TJS"), { target: { value: "0.99" } });
    fireEvent.click(screen.getByRole("button", { name: "Подготовить оплату" }));
    await screen.findByRole("alert");
    expect(network.preview).not.toHaveBeenCalled();
    expect(loadUnitPaymentMarker(saleId, scope)).toBeNull();
  });
  it("ignores late quote responses after unmount", async () => {
    let resolve: ((value: { quote: typeof quote; revision_hash: string }) => void) | undefined;
    network.quote.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const p = props();
    const view = render(<PhysicalPaymentPreparationPanel {...p} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Подготовка карты/QR" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Подготовка карты/QR" }));
    await waitFor(() => expect(network.quote).toHaveBeenCalled());
    view.unmount();
    const calls = p.onBlockedChange.mock.calls.length;
    await act(async () => {
      resolve?.({ quote, revision_hash: request.expected_revision_hash });
    });
    expect(p.onBlockedChange.mock.calls.length).toBe(calls);
    expect(network.preview).not.toHaveBeenCalled();
    expect(loadUnitPaymentMarker(saleId, scope)).toBeNull();
  });
});
