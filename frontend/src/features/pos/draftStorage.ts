/**
 * Per-user, tenant and register draft persistence. The live draft is
 * stashed in localStorage so a reload or accidental close restores the cart;
 * a stale draft (idle past the TTL) is dropped rather than reopened silently.
 */

import { hasPendingCompletion } from "./completionOperation";
import { loadPendingCheckoutOperation } from "./checkoutOperation";
import { loadPendingPosCommand } from "./commandOperation";
import { loadPendingPaymentOperation } from "./paymentOperation";
import { hasPaymentAttemptOperation } from "./paymentAttemptOperation";
import { currentPosStorageScope, posStorageKey, type PosStorageScope } from "./posStorageScope";
import { loadPhysicalCartRecovery } from "./physicalCartRecovery";
import { hasCashRecovery } from "./physicalCashRecoveryPresence";
import { hasUnitPaymentPreparation } from "./unitPaymentPreparationPresence";

export const legacyDraftKey = (registerId: string): string => `pos:currentSale:${registerId}`;
export const draftKey = (registerId: string, scope: PosStorageScope): string =>
  posStorageKey("pos:currentSale:v2:", registerId, scope);

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
  status?: "completed";
  nameById: Record<string, string>;
  expired: boolean;
  requiresRx: boolean;
  stagedPayments: SavedStagedPayment[];
  expiredSaleConfirmed?: boolean;
  externalPaymentReviewRequired: boolean;
}

export function scopedDraftStorage(scope: PosStorageScope) {
  return {
    saveDraft: (...args: Parameters<typeof saveDraft>): boolean => {
      args[8] = scope;
      return saveDraft(...args);
    },
    clearDraft: (registerId: string): boolean => clearDraft(registerId, scope),
  };
}

export function loadDraft(
  registerId: string,
  ttlMin: number = DRAFT_TTL_MIN,
  scope: PosStorageScope | null = currentPosStorageScope(),
): DraftInit {
  try {
    const raw = scope ? window.localStorage.getItem(draftKey(registerId, scope)) : null;
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
          !loadPendingPosCommand(registerId, scope) &&
          parsed.externalPaymentReviewRequired !== true &&
          !hasPaymentAttemptOperation(parsed.saleId) &&
          !loadPendingPaymentOperation(parsed.saleId) &&
          !hasPendingCompletion(parsed.saleId) &&
          !loadPendingCheckoutOperation(parsed.saleId) &&
          (!scope || !loadPhysicalCartRecovery(parsed.saleId, scope)) &&
          (!scope || !hasCashRecovery(parsed.saleId, scope)) &&
          (!scope || !hasUnitPaymentPreparation(parsed.saleId, scope))
        ) {
          // Stale: clear it and flag the cashier instead of reopening blind.
          if (scope) window.localStorage.removeItem(draftKey(registerId, scope));
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
          ...(parsed.status === "completed" ? { status: "completed" as const } : {}),
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
  scope: PosStorageScope | null = currentPosStorageScope(),
): boolean {
  if (!scope) return false;
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
    window.localStorage.setItem(draftKey(registerId, scope), serialized);
    return window.localStorage.getItem(draftKey(registerId, scope)) === serialized;
  } catch {
    return false;
  }
}

export function clearDraft(
  registerId: string,
  scope: PosStorageScope | null = currentPosStorageScope(),
): boolean {
  if (!scope) return false;
  try {
    window.localStorage.removeItem(draftKey(registerId, scope));
    return window.localStorage.getItem(draftKey(registerId, scope)) === null;
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
