/**
 * Per-register draft persistence for the POS register. The live draft is
 * stashed in localStorage so a reload or accidental close restores the cart;
 * a stale draft (idle past the TTL) is dropped rather than reopened silently.
 */

import { hasPendingCompletion } from "./completionOperation";
import { loadPendingCheckoutOperation } from "./checkoutOperation";
import { loadPendingPosCommand } from "./commandOperation";
import { loadPendingPaymentOperation } from "./paymentOperation";
import { hasPaymentAttemptOperation } from "./paymentAttemptOperation";

export const draftKey = (registerId: string): string => `pos:currentSale:${registerId}`;

// Fallback idle-TTL (minutes) when tenant settings haven't loaded yet. The real
// limit comes from tenant_settings.draft_sale_lifetime_min, passed into
// loadDraft. The savedAt stamp refreshes on every cart change → idle timeout.
export const DRAFT_TTL_MIN = 30;
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface SavedDraft {
  saleId: string;
  nameById: Record<string, string>;
  savedAt: number;
  status?: "draft" | "completed";
  requiresRx?: boolean;
  stagedPayments?: SavedStagedPayment[];
  expiredSaleConfirmed?: boolean;
  externalPaymentReviewRequired?: boolean;
}

export interface SavedStagedPayment {
  payment_method: "cash" | "card" | "qr";
  amount: string;
  payment_attempt_id?: string;
  metadata?: {
    cash_received?: string;
  };
}

export interface DraftInit {
  saleId: string | null;
  nameById: Record<string, string>;
  expired: boolean;
  requiresRx: boolean;
  stagedPayments: SavedStagedPayment[];
  expiredSaleConfirmed?: boolean;
  externalPaymentReviewRequired: boolean;
}

export function loadDraft(registerId: string, ttlMin: number = DRAFT_TTL_MIN): DraftInit {
  try {
    const raw = window.localStorage.getItem(draftKey(registerId));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<SavedDraft>;
      if (parsed && typeof parsed.saleId === "string") {
        const savedAt = typeof parsed.savedAt === "number" ? parsed.savedAt : 0;
        const ageMin = (Date.now() - savedAt) / 60_000;
        const stagedPayments = parseStagedPayments(parsed.stagedPayments);
        const hasElectronicPayment = stagedPayments.some(
          (payment) => payment.payment_method === "card" || payment.payment_method === "qr",
        );
        if (
          ageMin > ttlMin &&
          parsed.status !== "completed" &&
          !hasElectronicPayment &&
          !loadPendingPosCommand(registerId) &&
          parsed.externalPaymentReviewRequired !== true &&
          !hasPaymentAttemptOperation(parsed.saleId) &&
          !loadPendingPaymentOperation(parsed.saleId) &&
          !hasPendingCompletion(parsed.saleId) &&
          !loadPendingCheckoutOperation(parsed.saleId)
        ) {
          // Stale: clear it and flag the cashier instead of reopening blind.
          window.localStorage.removeItem(draftKey(registerId));
          return {
            saleId: null,
            nameById: {},
            expired: true,
            requiresRx: false,
            stagedPayments: [],
            externalPaymentReviewRequired: false,
          };
        }
        return {
          saleId: parsed.saleId,
          nameById: parsed.nameById ?? {},
          expired: false,
          requiresRx: parsed.requiresRx === true,
          stagedPayments,
          externalPaymentReviewRequired: parsed.externalPaymentReviewRequired === true,
          ...(parsed.expiredSaleConfirmed === true ? { expiredSaleConfirmed: true } : {}),
        };
      }
    }
  } catch {
    // ignore corrupt/blocked storage
  }
  return {
    saleId: null,
    nameById: {},
    expired: false,
    requiresRx: false,
    stagedPayments: [],
    externalPaymentReviewRequired: false,
  };
}

export function saveDraft(
  registerId: string,
  saleId: string,
  nameById: Record<string, string>,
  status: "draft" | "completed" = "draft",
  requiresRx: boolean = false,
  stagedPayments: readonly SavedStagedPayment[] = [],
  expiredSaleConfirmed: boolean = false,
  externalPaymentReviewRequired: boolean = false,
): boolean {
  const safePayments = parseStagedPayments(stagedPayments);
  if (safePayments.length !== stagedPayments.length) return false;
  const serialized = JSON.stringify({
    saleId,
    nameById,
    savedAt: Date.now(),
    status,
    requiresRx,
    stagedPayments: safePayments,
    expiredSaleConfirmed,
    externalPaymentReviewRequired,
  });
  try {
    window.localStorage.setItem(draftKey(registerId), serialized);
    return window.localStorage.getItem(draftKey(registerId)) === serialized;
  } catch {
    return false;
  }
}

export function clearDraft(registerId: string): boolean {
  try {
    window.localStorage.removeItem(draftKey(registerId));
    return window.localStorage.getItem(draftKey(registerId)) === null;
  } catch {
    return false;
  }
}

function parseStagedPayments(value: unknown): SavedStagedPayment[] {
  if (!Array.isArray(value) || value.length > 10) return [];

  const parsed: SavedStagedPayment[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) return [];
    const method = entry.payment_method;
    const amount = entry.amount;
    if (
      (method !== "cash" && method !== "card" && method !== "qr") ||
      typeof amount !== "string" ||
      !isPositiveMoney(amount)
    ) {
      return [];
    }

    const paymentAttemptId = entry.payment_attempt_id;
    if (
      (method === "card" || method === "qr") &&
      (typeof paymentAttemptId !== "string" || !UUID_V4_PATTERN.test(paymentAttemptId))
    ) {
      return [];
    }
    if (method === "cash" && paymentAttemptId !== undefined) return [];

    const payment: SavedStagedPayment = {
      payment_method: method,
      amount,
      ...(typeof paymentAttemptId === "string" ? { payment_attempt_id: paymentAttemptId } : {}),
    };
    if (entry.metadata !== undefined) {
      if (!isRecord(entry.metadata)) return [];
      const cashReceived = entry.metadata.cash_received;
      if (Object.keys(entry.metadata).some((key) => key !== "cash_received")) return [];
      if (
        cashReceived !== undefined &&
        (method !== "cash" ||
          typeof cashReceived !== "string" ||
          !isPositiveMoney(cashReceived) ||
          Number(cashReceived) + 0.001 < Number(amount))
      ) {
        return [];
      }
      if (typeof cashReceived === "string") {
        payment.metadata = { cash_received: cashReceived };
      }
    }
    parsed.push(payment);
  }
  return parsed;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveMoney(value: string): boolean {
  if (!/^(?:0|[1-9]\d{0,11})\.\d{2}$/.test(value)) return false;
  const amount = Number(value);
  return Number.isFinite(amount) && amount > 0;
}
