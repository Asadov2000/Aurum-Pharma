import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { matchesElectronicCompletedSale } from "@/features/pos/electronicCheckoutReceipt";
import { parseMixedCashCheckoutResult } from "@/features/pos/mixedCashCheckout";
import {
  acknowledgeElectronicCheckout,
  cancelElectronicPreparation,
  executeElectronicCommand,
  loadElectronicCommand,
  parseElectronicResult,
  recoverElectronicCheckout,
  type ElectronicCommand,
} from "@/features/pos/unitElectronicCheckout";
import {
  loadUnitPaymentMarker,
  storeUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";
import { authenticatePos } from "./posIdentityFixture";
import { installElectronicLocks, context, result, attempt } from "./unitElectronicCheckoutFixture";
import { id, scope } from "./unitPaymentPreparationFixture";
import { mixedAttempts, mixedContext, mixedResult } from "./mixedElectronicCheckoutFixture";

beforeEach(() => {
  installElectronicLocks();
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  authenticatePos(scope);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("mixed electronic cashier contracts and recovery", () => {
  it("serializes mixed cancellation behind another tab's attempt creation", async () => {
    const c = mixedContext(),
      rows = mixedAttempts(c, "pending");
    storeUnitPaymentMarker(c.intent.plan.request.sale_id, scope, c.intent.operation_id);
    let applied = false,
      resolve: (value: { data: unknown }) => void = () => undefined;
    const delayed = new Promise<{ data: unknown }>((done) => {
      resolve = done;
    });
    const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).endsWith("/binding")) return { data: c.binding };
      if (!applied || String(url).endsWith("/checkout"))
        throw { isAxiosError: true, response: { status: 404 } };
      return { data: rows };
    });
    const post = vi.spyOn(api, "post").mockReturnValue(delayed);
    const creating = executeElectronicCommand(c, {
      action: "attempts",
      payload: { attempt_ids: { card: id(21) } },
    });
    await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const cancel = vi.fn();
    const cancelling = cancelElectronicPreparation(c, cancel).then(
      () => true,
      () => false,
    );
    await Promise.resolve();
    expect(get.mock.calls.some((call) => String(call[0]).endsWith("/binding"))).toBe(false);
    applied = true;
    resolve({ data: rows });
    await creating;
    expect(await cancelling).toBe(false);
    expect(cancel).not.toHaveBeenCalled();
    expect(loadUnitPaymentMarker(c.intent.plan.request.sale_id, scope)).toBe(c.intent.operation_id);
    expect(post).toHaveBeenCalledTimes(1);
  });
  it.each(["attempts", "reconciliation", "confirm", "void", "complete"] as const)(
    "sends only the explicit mixed %s command and resolves its exact facts",
    async (action) => {
      const c = mixedContext("combined"),
        rows = mixedAttempts(c, "pending");
      storeUnitPaymentMarker(c.intent.plan.request.sale_id, scope, c.intent.operation_id);
      const card = rows.find((row) => row.payment_method === "card")!;
      const command: ElectronicCommand =
        action === "attempts"
          ? { action, payload: { attempt_ids: { card: id(21), qr: id(22) } } }
          : action === "complete"
            ? { action, payload: {} }
            : action === "confirm"
              ? {
                  action,
                  attemptId: card.id,
                  payload: { terminal_id: "T1", external_reference: "R1" },
                }
              : action === "void"
                ? { action, attemptId: card.id, payload: { reason: "cashier_cancelled" } }
                : { action, attemptId: card.id, payload: {} };
      let applied = false;
      const resolved = () =>
        action === "complete"
          ? mixedAttempts(c)
          : action === "attempts"
            ? rows
            : rows.map((row) =>
                row.id !== card.id
                  ? row
                  : {
                      ...mixedAttempts(
                        c,
                        action === "confirm"
                          ? "confirmed"
                          : action === "void"
                            ? "voided"
                            : "requires_reconciliation",
                      ).find((value) => value.id === row.id)!,
                      ...(action === "void"
                        ? { void_reason: "cashier_cancelled", voided_at: "2026-10-20T12:03:00Z" }
                        : {}),
                    },
              );
      vi.spyOn(api, "get").mockImplementation(async (url) => {
        if (!applied || (String(url).endsWith("/checkout") && action !== "complete"))
          throw { isAxiosError: true, response: { status: 404 } };
        return { data: String(url).endsWith("/checkout") ? mixedResult(c) : resolved() };
      });
      const post = vi.spyOn(api, "post").mockImplementation(async () => {
        expect(loadElectronicCommand(c)).toEqual(command);
        applied = true;
        return {
          data:
            action === "complete"
              ? mixedResult(c)
              : action === "attempts"
                ? rows
                : resolved().find((row) => row.id === card.id),
        };
      });
      await executeElectronicCommand(c, command);
      expect(post).toHaveBeenCalledTimes(1);
      expect(post.mock.calls[0]?.[0]).toContain("/mixed-checkout/");
      expect(post.mock.calls[0]?.[1]).toEqual(command.payload);
      expect(loadElectronicCommand(c)).toBeNull();
    },
  );
  it("mixed cancellation reads actual binding and attempts before allowing cancellation", async () => {
    const c = mixedContext(),
      cancelled = {
        ...c.binding,
        state: "cancelled" as const,
        cancelled_at: "2026-10-20T12:04:00Z",
      };
    storeUnitPaymentMarker(c.intent.plan.request.sale_id, scope, c.intent.operation_id);
    const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
      if (String(url).endsWith("/binding")) return { data: c.binding };
      if (String(url).endsWith("/checkout"))
        throw { isAxiosError: true, response: { status: 404 } };
      return { data: mixedAttempts(c, "pending") };
    });
    const cancel = vi.fn().mockResolvedValue(cancelled);
    await expect(cancelElectronicPreparation(c, cancel)).rejects.toThrow();
    expect(cancel).not.toHaveBeenCalled();
    expect(
      get.mock.calls.some((call) => String(call[0]).endsWith("/mixed-checkout/attempts")),
    ).toBe(true);
    get.mockImplementation(async (url) => {
      if (String(url).endsWith("/binding")) return { data: c.binding };
      if (String(url).endsWith("/checkout"))
        throw { isAxiosError: true, response: { status: 404 } };
      return { data: mixedAttempts(c, "voided") };
    });
    await cancelElectronicPreparation(c, cancel);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(loadUnitPaymentMarker(c.intent.plan.request.sale_id, scope)).toBeNull();
  });
  it.each(["card", "qr", "combined"] as const)(
    "recovers exact mixed V4 %s with GET only",
    async (method) => {
      const c = mixedContext(method),
        doc = mixedResult(c),
        rows = mixedAttempts(c);
      const get = vi.spyOn(api, "get").mockImplementation(async (url) => ({
        data: String(url).endsWith("/checkout") ? doc : rows,
      }));
      const post = vi.spyOn(api, "post");
      expect(parseElectronicResult(doc, c, rows).document_kind).toBe("mixed_checkout");
      expect((await recoverElectronicCheckout(c)).result).toEqual(doc);
      expect(get.mock.calls.map((call) => call[0])).toEqual([
        expect.stringMatching(/\/mixed-checkout\/checkout$/),
        expect.stringMatching(/\/mixed-checkout\/attempts$/),
      ]);
      expect(post).not.toHaveBeenCalled();
    },
  );
  it("resolves a lost complete using GET facts and accepts only the exact historical receipt", async () => {
    const c = mixedContext("combined"),
      doc = mixedResult(c),
      rows = mixedAttempts(c);
    storeUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
    const command = { action: "complete" as const, payload: {} };
    const post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost response"));
    await expect(executeElectronicCommand(c, command)).rejects.toThrow("lost response");
    expect(post.mock.calls[0]?.[0]).toMatch(/\/mixed-checkout\/complete$/);
    expect(loadElectronicCommand(c)).toEqual(command);
    vi.spyOn(api, "get").mockImplementation(async (url) => ({
      data: String(url).endsWith("/checkout") ? doc : rows,
    }));
    await recoverElectronicCheckout(c);
    expect(loadElectronicCommand(c)).toBeNull();
    expect(loadUnitPaymentMarker(doc.sale_id, scope)).toBe(doc.operation_id);
    const accept = vi.fn().mockResolvedValue(true);
    expect(
      await acknowledgeElectronicCheckout(c, parseElectronicResult(doc, c, rows), accept),
    ).toBe(true);
    expect(accept).toHaveBeenCalledWith(doc);
    expect(loadUnitPaymentMarker(doc.sale_id, scope)).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("rejects mixed/physical substitution, changed legacy allocation and incomplete money facts", () => {
    const c = mixedContext("combined"),
      doc = mixedResult(c),
      rows = mixedAttempts(c);
    for (const changed of [
      { ...doc, schema_version: 3, document_kind: "physical_checkout" },
      {
        ...doc,
        lines: doc.lines.map((l) =>
          l.inventory_kind === "legacy_pack" ? { ...l, batch_id: id(99) } : l,
        ),
      },
      {
        ...doc,
        lines: doc.lines.map((l) =>
          l.inventory_kind === "legacy_pack" ? { ...l, quantity: "0.002" } : l,
        ),
      },
      { ...doc, lines: [...doc.lines].reverse() },
      { ...doc, payments: doc.payments.slice(1) },
      { ...doc, payments: doc.payments.map((p) => (p.method === "qr" ? { ...p, id: id(99) } : p)) },
    ])
      expect(() => parseElectronicResult(changed, c, rows)).toThrow();
    expect(() => parseElectronicResult(doc, c, mixedAttempts(c, "confirmed"))).toThrow();
    expect(() => parseElectronicResult(doc, context(), [attempt("consumed")])).toThrow();
    expect(() => parseElectronicResult(result(), c, rows)).toThrow();
  });
  it("preserves cash-only parser boundary and canonical separate electronic posting IDs", () => {
    const c = mixedContext("combined"),
      doc = parseElectronicResult(mixedResult(c), c, mixedAttempts(c));
    expect(() =>
      parseMixedCashCheckoutResult(
        doc,
        {
          schema_version: 2,
          operation_id: doc.operation_id,
          sale_id: doc.sale_id,
          expected_revision_hash: c.intent.plan.request.expected_revision_hash,
          expected_total_amount: doc.total_amount,
          cash_tendered: "3.00",
          currency: "TJS",
        },
        scope,
      ),
    ).toThrow();
    const sale = {
      status: "completed" as const,
      is_test: false,
      id: doc.sale_id,
      tenant_id: doc.tenant_id,
      cashier_user_id: doc.cashier_id,
      branch_id: doc.branch_id,
      register_id: doc.register_id,
      shift_id: doc.shift_id,
      receipt_number: doc.receipt_number,
      total_amount: doc.total_amount,
      currency: doc.currency,
      payments: doc.payments.map((p, i) => ({
        id: p.id,
        sale_id: doc.sale_id,
        operation_id: p.method === "cash" ? p.id : id(70 + i),
        payment_method: p.method,
        amount: p.amount,
        currency: p.currency,
      })),
    };
    expect(matchesElectronicCompletedSale(sale, doc)).toBe(true);
    expect(matchesElectronicCompletedSale({ ...sale, register_id: id(99) }, doc)).toBe(false);
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: sale.payments.map((p) => ({ ...p, operation_id: p.id })) },
        doc,
      ),
    ).toBe(false);
  });
  it("never accepts or clears a lost command after an awaited register fence changes", async () => {
    const c = mixedContext(),
      doc = mixedResult(c);
    let active = true;
    const owner = { ...c, isActive: () => active };
    storeUnitPaymentMarker(doc.sale_id, scope, doc.operation_id);
    vi.spyOn(api, "post").mockRejectedValue(new Error("lost"));
    await expect(
      executeElectronicCommand(owner, { action: "complete", payload: {} }),
    ).rejects.toThrow();
    vi.spyOn(api, "get").mockImplementation(async () => {
      active = false;
      return { data: doc };
    });
    await expect(recoverElectronicCheckout(owner)).rejects.toThrow();
    expect(loadElectronicCommand(c)?.action).toBe("complete");
    expect(loadUnitPaymentMarker(doc.sale_id, scope)).toBe(doc.operation_id);
  });
});
