import { createHash, webcrypto } from "node:crypto";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhysicalCashPanel } from "@/features/pos/PhysicalCashPanel";
import { type PhysicalCartItem } from "@/features/pos/physicalCart";
import {
  cashConfirmationRequestSchema,
  type CashConfirmationRequest,
  type SavedPhysicalCashIntent,
} from "@/features/pos/physicalCashCheckout";
import {
  loadPendingPhysicalCash,
  physicalCashRecoveryKey,
  runPhysicalCash,
} from "@/features/pos/physicalCashRecovery";
import { mixedCashRecoveryKey } from "@/features/pos/physicalCashRecoveryPresence";
import { api, configureAuthHooks } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos } from "./posIdentityFixture";

const id = (suffix: number) => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
const scope = { tenantId: id(1), userId: id(2) };
const saleId = id(3);
const branchId = id(4);
const registerId = id(5);
const packaging = {
  version: 2 as const,
  unit: "tablet" as const,
  units_per_pack: 30,
  units_per_blister: 10,
  dispense_step: 1,
};
const item: PhysicalCartItem = {
  id: id(6),
  tenant_id: scope.tenantId,
  branch_id: branchId,
  sale_id: saleId,
  catalog_id: id(7),
  position: 1,
  sale_format: "unit",
  quantity: 61,
  discount_amount: "0.00",
  physical_packaging: packaging,
};
const quote = {
  schema_version: 2 as const,
  state: "draft_quote" as const,
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: saleId,
  branch_id: branchId,
  register_id: registerId,
  shift_id: id(8),
  lines: [
    {
      inventory_kind: "physical_unit" as const,
      id: item.id,
      position: 1,
      catalog_id: item.catalog_id,
      packaging,
      sale_format: "unit" as const,
      quantity: 61,
      base_units: 61,
      original_retail_unit_price: "1.00",
      gross_amount: "61.00",
      discount_amount: "0.00",
      net_amount: "61.00",
      money_version: 1 as const,
      currency: "TJS" as const,
    },
  ],
  total_amount: "61.00",
  currency: "TJS" as const,
};

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, itemValue]) => `${JSON.stringify(key)}:${canonical(itemValue)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

const reviewed = {
  quote,
  revision_hash: createHash("sha256").update(canonical(quote)).digest("hex"),
};
const insufficient = {
  error: {
    code: "business_rule_violation",
    message: "Insufficient compatible physical stock for cart",
  },
};
const originalAdapter = api.defaults.adapter;
const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");

function modelTransport() {
  let saved: SavedPhysicalCashIntent | null = null;
  let terminal: "saved_draft" | "cancelled" | "completed" = "saved_draft";
  let behavior = "cancelled";
  let unknownStatus = false;
  let gate: Promise<void> | null = null;
  let nextStatusGate: Promise<void> | null = null;
  const calls = { quote: 0, status: 0, intent: 0, complete: 0, cancel: 0 };
  function result() {
    if (!saved) throw new Error("Missing synthetic intent");
    const request = saved.confirmation.request;
    return {
      tenant_id: scope.tenantId,
      cashier_id: scope.userId,
      sale_id: saleId,
      branch_id: branchId,
      register_id: registerId,
      shift_id: quote.shift_id,
      schema_version: 3,
      document_kind: "physical_checkout",
      total_amount: "61.00",
      currency: "TJS",
      operation_id: request.operation_id,
      event_id: request.operation_id,
      stock_command_id: request.operation_id,
      created_at: "2026-11-04T09:59:00Z",
      completed_at: "2026-11-04T10:01:00Z",
      receipt_number: "SYNTHETIC",
      receipt_sequence: 1,
      is_test: false,
      lines: quote.lines.map((line) => ({ ...line, name: "Синтетический товар" })),
      payments: [{ id: request.operation_id, method: "cash", amount: "61.00", currency: "TJS" }],
    };
  }
  function status(operationId: string | undefined) {
    if (!saved || unknownStatus)
      return { state: "not_found", operation_id: operationId, sale_id: saleId };
    const base = {
      state: terminal,
      operation_id: operationId,
      sale_id: saleId,
      saved_intent: saved,
    };
    return terminal === "completed" ? { ...base, result: result() } : base;
  }
  function response(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse<unknown> {
    return { data, status: 200, statusText: "OK", headers: {}, config };
  }
  api.defaults.adapter = async (config) => {
    if (config.method === "get" && config.url === `/sales/${saleId}/physical-cart/quote`) {
      calls.quote += 1;
      return response(config, reviewed);
    }
    if (
      config.method === "get" &&
      config.url?.startsWith(`/sales/${saleId}/physical-cash/operations/`)
    ) {
      calls.status += 1;
      const waiting = nextStatusGate;
      nextStatusGate = null;
      if (waiting) await waiting;
      const operationId = config.url.split("/").at(-1);
      return response(config, status(operationId));
    }
    if (config.method === "post" && config.url === `/sales/${saleId}/physical-cash/intent`) {
      calls.intent += 1;
      const request = cashConfirmationRequestSchema.parse(
        JSON.parse(String(config.data)) as unknown,
      );
      saved = {
        schema_version: 2,
        state: "saved_draft",
        accepted_at: "2026-11-04T10:00:00Z",
        confirmation: {
          schema_version: 2,
          state: "validated_draft",
          request,
          quote,
          change_amount: "0.00",
        },
      };
      return response(config, saved);
    }
    if (config.method === "post" && config.url === `/sales/${saleId}/physical-cash/complete`) {
      calls.complete += 1;
      const request = cashConfirmationRequestSchema.parse(
        JSON.parse(String(config.data)) as unknown,
      );
      expect(saved?.confirmation.request).toEqual(request);
      const rejected = {
        ...response(config, insufficient),
        status: 422,
        statusText: "Unprocessable Entity",
      };
      throw new AxiosError(
        insufficient.error.message,
        AxiosError.ERR_BAD_REQUEST,
        config,
        undefined,
        rejected,
      );
    }
    if (config.method === "post" && config.url === `/sales/${saleId}/physical-cash/cancel`) {
      calls.cancel += 1;
      const request = cashConfirmationRequestSchema.parse(
        JSON.parse(String(config.data)) as unknown,
      );
      expect(request).toEqual(saved?.confirmation.request);
      if (gate) await gate;
      if (behavior === "network") throw new AxiosError("Synthetic network failure");
      if (behavior === "conflict")
        throw new AxiosError("Synthetic conflict", AxiosError.ERR_BAD_REQUEST, config, undefined, {
          ...response(config, {}),
          status: 409,
        });
      if (behavior === "nonterminal") return response(config, status(request.operation_id));
      terminal = "cancelled";
      const payload = status(request.operation_id);
      if (behavior === "lost") throw new AxiosError("Synthetic lost cancellation response");
      if (behavior === "stale_get") terminal = "saved_draft";
      if (behavior === "wrong_owner" && saved)
        return response(config, {
          ...payload,
          saved_intent: {
            ...saved,
            confirmation: { ...saved.confirmation, quote: { ...quote, cashier_id: id(99) } },
          },
        });
      if (behavior === "logout") useAuthStore.getState().clear();
      if (behavior === "changed_marker") {
        const pending = loadPendingPhysicalCash(saleId, scope);
        if (!pending) throw new Error("Missing synthetic pending marker");
        localStorage.setItem(
          physicalCashRecoveryKey(saleId, scope),
          JSON.stringify({ ...pending, createdAt: pending.createdAt + 1 }),
        );
      }
      return response(config, payload);
    }
    throw new Error("Unexpected synthetic cash request");
  };
  return {
    calls,
    request: (): CashConfirmationRequest | undefined => saved?.confirmation.request,
    setBehavior(value: string) {
      behavior = value;
    },
    completeElsewhere() {
      terminal = "completed";
    },
    hideStatus() {
      unknownStatus = true;
    },
    holdCancellation() {
      let release!: () => void;
      gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      return release;
    },
    holdNextStatus() {
      let release!: () => void;
      nextStatusGate = new Promise<void>((resolve) => {
        release = resolve;
      });
      return release;
    },
  };
}

function serialLocks(): void {
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (_name: string, execute: () => Promise<unknown>) => {
        const current = tail.then(execute);
        tail = current.catch(() => undefined);
        return current;
      },
    },
  });
}

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  localStorage.clear();
  authenticatePos(scope);
  useAuthStore.setState({ accessToken: "synthetic-access" });
  configureAuthHooks({
    getAccessToken: () => useAuthStore.getState().accessToken,
    refreshTokens: async () => null,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {},
  });
  serialLocks();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  api.defaults.adapter = originalAdapter;
  useAuthStore.getState().clear();
  configureAuthHooks({
    getAccessToken: () => null,
    refreshTokens: async () => null,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {},
  });
  localStorage.clear();
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks);
  else Reflect.deleteProperty(navigator, "locks");
  vi.unstubAllGlobals();
});

async function declineThroughRealPanel() {
  const transport = modelTransport();
  const blocked = vi.fn<(value: boolean) => void>();
  const completed = vi.fn(async () => true);
  const props = {
    saleId,
    branchId,
    registerId,
    scope,
    items: [item],
    online: true,
    canPay: true,
    sessionIsActive: () => true,
    onBlockedChange: blocked,
    onCompleted: completed,
  };
  const view = render(<PhysicalCashPanel {...props} />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Завершить продажу" })).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText("Получено наличными, TJS"), { target: { value: "61" } });
  fireEvent.click(screen.getByRole("button", { name: "Завершить продажу" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Действие невозможно по правилам системы.",
  );
  const pending = loadPendingPhysicalCash(saleId, scope);
  if (!pending) throw new Error("The rejected payment lost its recovery marker");
  expect(pending.request).toEqual(transport.request());
  await screen.findByRole("button", { name: "Вернуться к корзине" });
  expect(blocked).toHaveBeenLastCalledWith(true);
  expect(completed).not.toHaveBeenCalled();
  return { transport, blocked, completed, props, view, pending };
}

describe("physical cash definite stock-refusal recovery", () => {
  // The baseline characterization passed before the fix: the panel stayed blocked
  // after this modeled HTTP422 and a GET-only remount. This remains a transport
  // model; the separate Playwright case exercises the real disposable backend.
  it("keeps refusal pending until explicit terminal cancellation, then releases the cart", async () => {
    const { transport, blocked, completed, props, view, pending } = await declineThroughRealPanel();
    const panel = screen.getByRole("region", { name: "Оплата наличными" });
    expect(
      within(panel)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Проверить и завершить оплату", "Вернуться к корзине"]);
    view.unmount();
    render(<PhysicalCashPanel {...props} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    expect(loadPendingPhysicalCash(saleId, scope)).toEqual(pending);
    expect(transport.calls.cancel).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(loadPendingPhysicalCash(saleId, scope)).toBeNull();
    expect(transport.calls.cancel).toBe(1);
    expect(transport.calls.complete).toBe(1);
    expect(completed).not.toHaveBeenCalled();
    await screen.findByLabelText("Получено наличными, TJS");
  });

  it("recovers a lost cancellation response with terminal GET only and never repeats completion", async () => {
    const { transport, blocked, completed, props, view } = await declineThroughRealPanel();
    transport.setBehavior("lost");
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    expect(loadPendingPhysicalCash(saleId, scope)).not.toBeNull();
    expect(blocked).toHaveBeenLastCalledWith(true);
    await expect(runPhysicalCash(saleId, scope)).rejects.toThrow("Оплата отменена");
    expect(transport.calls.complete).toBe(1);
    view.unmount();
    render(<PhysicalCashPanel {...props} />);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(loadPendingPhysicalCash(saleId, scope)).toBeNull();
    expect(transport.calls.cancel).toBe(1);
    expect(transport.calls.complete).toBe(1);
    expect(completed).not.toHaveBeenCalled();
  });

  it("displays a completed winner through existing receipt acknowledgement without cancelling it", async () => {
    const { transport, blocked, completed } = await declineThroughRealPanel();
    transport.completeElsewhere();
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() => expect(completed).toHaveBeenCalledOnce());
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(loadPendingPhysicalCash(saleId, scope)).toBeNull();
    expect(transport.calls.cancel).toBe(0);
    expect(transport.calls.complete).toBe(1);
    expect(screen.getByText("Продажа завершена. Чек сохранён.")).toBeInTheDocument();
  });

  it.each([
    "not_found",
    "nonterminal",
    "stale_get",
    "network",
    "conflict",
    "wrong_owner",
    "logout",
    "opposite",
  ])(
    "retains the recovery marker and block on %s instead of guessing that cancellation succeeded",
    async (failure) => {
      const { transport, blocked, completed, pending } = await declineThroughRealPanel();
      if (failure === "not_found") transport.hideStatus();
      else if (failure === "opposite")
        localStorage.setItem(mixedCashRecoveryKey(saleId, scope), "{}");
      else transport.setBehavior(failure);
      fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
      );
      expect(loadPendingPhysicalCash(saleId, scope)).toEqual(pending);
      expect(blocked).toHaveBeenLastCalledWith(true);
      expect(completed).not.toHaveBeenCalled();
      expect(transport.calls.complete).toBe(1);
    },
  );

  it("does not remove a replaced recovery marker", async () => {
    const { transport, blocked, completed, pending } = await declineThroughRealPanel();
    transport.setBehavior("changed_marker");
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    expect(loadPendingPhysicalCash(saleId, scope)?.createdAt).toBe(pending.createdAt + 1);
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(completed).not.toHaveBeenCalled();
  });

  it("keeps the marker if terminal acknowledgement cannot be stored", async () => {
    const { blocked, completed, pending } = await declineThroughRealPanel();
    const original = Storage.prototype.removeItem;
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === physicalCashRecoveryKey(saleId, scope))
        throw new DOMException("Synthetic storage failure");
      return original.call(this, key);
    });
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    expect(loadPendingPhysicalCash(saleId, scope)).toEqual(pending);
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(completed).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("serializes cancellation controls and ignores continuation while cancellation is in flight", async () => {
    const { transport, blocked } = await declineThroughRealPanel();
    const release = transport.holdCancellation();
    const cancel = screen.getByRole("button", { name: "Вернуться к корзине" });
    const resume = screen.getByRole("button", { name: "Проверить и завершить оплату" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    fireEvent.click(resume);
    await waitFor(() => expect(transport.calls.cancel).toBe(1));
    expect(transport.calls.complete).toBe(1);
    release();
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(transport.calls.cancel).toBe(1);
  });

  it("does not start automatic reconciliation on reconnect while explicit cancellation is pending", async () => {
    const { transport, blocked, props, view } = await declineThroughRealPanel();
    view.unmount();
    const reloaded = render(<PhysicalCashPanel {...props} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    const release = transport.holdCancellation();
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() => expect(transport.calls.cancel).toBe(1));
    const readsDuringCancellation = transport.calls.status;
    await act(async () => {
      reloaded.rerender(<PhysicalCashPanel {...props} online={false} />);
    });
    await act(async () => {
      reloaded.rerender(<PhysicalCashPanel {...props} online />);
    });
    expect(transport.calls.status).toBe(readsDuringCancellation);
    expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить и завершить оплату" })).toBeDisabled();
    release();
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(transport.calls.cancel).toBe(1);
    expect(transport.calls.complete).toBe(1);
  });

  it("restarts an aborted mount read on reconnect without letting its old cleanup unlock a newer action", async () => {
    const { transport, blocked, props, view } = await declineThroughRealPanel();
    view.unmount();
    const releaseOldRead = transport.holdNextStatus();
    const reloaded = render(<PhysicalCashPanel {...props} />);
    await waitFor(() => expect(transport.calls.status).toBe(2));
    await act(async () => {
      reloaded.rerender(<PhysicalCashPanel {...props} online={false} />);
    });
    await act(async () => {
      reloaded.rerender(<PhysicalCashPanel {...props} online />);
    });
    await waitFor(() => expect(transport.calls.status).toBe(3));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeEnabled(),
    );
    const releaseCancel = transport.holdCancellation();
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() => expect(transport.calls.cancel).toBe(1));
    await act(async () => {
      releaseOldRead();
    });
    expect(screen.getByRole("button", { name: "Вернуться к корзине" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить и завершить оплату" })).toBeDisabled();
    releaseCancel();
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(transport.calls.cancel).toBe(1);
    expect(transport.calls.complete).toBe(1);
  });

  it("retains the marker when unmounted during cancellation, then acknowledges it read-only", async () => {
    const { transport, blocked, props, view, pending } = await declineThroughRealPanel();
    const release = transport.holdCancellation();
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к корзине" }));
    await waitFor(() => expect(transport.calls.cancel).toBe(1));
    view.unmount();
    release();
    // Reconcile through the real WebLock; the old unmounted action must leave the marker.
    await navigator.locks.request(physicalCashRecoveryKey(saleId, scope), () => {
      expect(loadPendingPhysicalCash(saleId, scope)).toEqual(pending);
    });
    render(<PhysicalCashPanel {...props} />);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(loadPendingPhysicalCash(saleId, scope)).toBeNull();
    expect(transport.calls.cancel).toBe(1);
    expect(transport.calls.complete).toBe(1);
  });
});
