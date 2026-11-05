import {
  mixedElectronicRefundGuard,
  mixedElectronicRefundAuthority,
} from "./mixedElectronicRefundAccess";
import { z } from "zod";

import {
  parseSavedMixedElectronicRefundActivation,
  type MixedElectronicRefundActivationScope,
  type SavedMixedElectronicRefundActivation,
} from "@/features/sales/mixedElectronicRefundActivation";
import {
  mixedElectronicRefundSaveRequestSchema,
  type MixedElectronicRefundSaveRequest,
} from "@/features/sales/mixedElectronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

const uuid = z.string().uuid();
const scopeSchema = z
  .object({
    tenantId: uuid,
    userId: uuid,
    parentSaleId: uuid,
    branchId: uuid,
    registerId: uuid,
    contextCheck: z.custom<() => void>((value) => typeof value === "function").optional(),
  })
  .strict();
const evidenceText = (limit: number) =>
  z
    .string()
    .trim()
    .min(1)
    .refine(
      (value) =>
        Array.from(value).length <= limit &&
        Array.from(value).every((char) => char.charCodeAt(0) >= 32 && char.charCodeAt(0) !== 127),
    );
export const mixedElectronicRefundConfirmationSchema = z
  .object({
    original_payment_id: uuid,
    terminal_id: evidenceText(64),
    document_number: evidenceText(128),
  })
  .strict();
export type MixedElectronicRefundConfirmation = z.infer<
  typeof mixedElectronicRefundConfirmationSchema
>;
const timestamp = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)));
const savedConfirmationSchema = mixedElectronicRefundConfirmationSchema
  .extend({
    terminal_id: z
      .string()
      .refine((value) => value === value.trim())
      .pipe(evidenceText(64)),
    document_number: z
      .string()
      .refine((value) => value === value.trim())
      .pipe(evidenceText(128)),
    method: z.enum(["card", "qr"]),
    amount: z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/),
    currency: z.literal("TJS"),
    recorded_at: timestamp,
  })
  .strict();
const reconciliationSchema = z
  .object({
    state: z.enum(["requires_reconciliation", "confirmed_evidence_only"]),
    activation: z.unknown(),
    confirmations: z.array(savedConfirmationSchema).max(10),
    missing_payment_ids: z.array(uuid).max(10),
  })
  .strict();
export type ReconciledMixedElectronicRefund = Omit<
  z.infer<typeof reconciliationSchema>,
  "activation"
> & {
  activation: SavedMixedElectronicRefundActivation;
};

function precedes(left: string, right: string): boolean {
  const seconds = (value: string): number => Date.parse(value.replace(/\.\d+/, ""));
  if (seconds(left) !== seconds(right)) return seconds(left) < seconds(right);
  const fraction = (value: string): string => value.match(/\.(\d+)/)?.[1] ?? "";
  const a = fraction(left);
  const b = fraction(right);
  const width = Math.max(a.length, b.length);
  return a.padEnd(width, "0") < b.padEnd(width, "0");
}

async function requestConfirmations(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  activation: SavedMixedElectronicRefundActivation,
  confirmation?: MixedElectronicRefundConfirmation,
): Promise<ReconciledMixedElectronicRefund> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  const expected = parseSavedMixedElectronicRefundActivation(activation, validatedScope, validated);
  const submitted =
    confirmation === undefined
      ? undefined
      : mixedElectronicRefundConfirmationSchema.parse(confirmation);
  const payments = new Map(
    expected.review.preview.payments
      .filter((part) => part.method !== "cash")
      .map((part) => [part.original_payment_id, part]),
  );
  if (submitted && !payments.has(submitted.original_payment_id))
    throw new Error("Подтверждение относится к другому электронному платежу.");
  const requireActive = mixedElectronicRefundGuard(
    validatedScope,
    submitted !== undefined,
    submitted !== undefined,
  );
  requireActive();
  const path = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/mixed-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}/confirmations`;
  const config = withAuthSession({ timeout: 8_000 });
  if (submitted !== undefined)
    await mixedElectronicRefundAuthority(validatedScope, requireActive, undefined);
  requireActive();
  const response = submitted
    ? await api.post<unknown>(path, submitted, config)
    : await api.get<unknown>(path, config);
  requireActive();
  return parseMixedElectronicRefundConfirmations(
    response.data,
    validatedScope,
    validated,
    expected,
    submitted,
  );
}

export function parseMixedElectronicRefundConfirmations(
  data: unknown,
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  activation: SavedMixedElectronicRefundActivation,
  submitted?: MixedElectronicRefundConfirmation,
): ReconciledMixedElectronicRefund {
  const validatedScope = scopeSchema.parse(scope);
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  const expected = parseSavedMixedElectronicRefundActivation(activation, validatedScope, validated);
  const payments = new Map(
    expected.review.preview.payments
      .filter((part) => part.method !== "cash")
      .map((part) => [part.original_payment_id, part]),
  );
  const result = reconciliationSchema.parse(data);
  const marker = parseSavedMixedElectronicRefundActivation(
    result.activation,
    validatedScope,
    validated,
  );
  if (JSON.stringify(marker) !== JSON.stringify(expected))
    throw new Error("Подтверждения относятся к другому сохранённому маркеру активации.");
  const seen = new Set<string>();
  const documents = new Set<string>();
  if (
    result.confirmations.some(
      (p, i) => i > 0 && p.original_payment_id <= result.confirmations[i - 1]!.original_payment_id,
    ) ||
    result.missing_payment_ids.some((id, i) => i > 0 && id <= result.missing_payment_ids[i - 1]!)
  )
    throw new Error("Порядок платежей не соответствует сохранённому результату.");
  for (const entry of result.confirmations) {
    const part = payments.get(entry.original_payment_id);
    const document = JSON.stringify([entry.terminal_id, entry.document_number]);
    if (
      !part ||
      seen.has(entry.original_payment_id) ||
      documents.has(document) ||
      part.method !== entry.method ||
      part.amount !== entry.amount ||
      precedes(entry.recorded_at, marker.activated_at)
    )
      throw new Error("Реквизиты подтверждения не соответствуют исходным частям возврата.");
    seen.add(entry.original_payment_id);
    documents.add(document);
  }
  const missing = new Set(result.missing_payment_ids);
  if (
    missing.size !== result.missing_payment_ids.length ||
    missing.size + seen.size !== payments.size ||
    [...missing].some((id) => !payments.has(id) || seen.has(id)) ||
    (result.state === "confirmed_evidence_only") !== (missing.size === 0) ||
    (submitted &&
      !result.confirmations.some(
        (entry) =>
          entry.original_payment_id === submitted.original_payment_id &&
          entry.terminal_id === submitted.terminal_id &&
          entry.document_number === submitted.document_number,
      ))
  )
    throw new Error("Набор подтверждённых и недостающих платежей не соответствует возврату.");
  return { ...result, activation: marker };
}

export async function recordMixedElectronicRefundConfirmation(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  activation: SavedMixedElectronicRefundActivation,
  confirmation: MixedElectronicRefundConfirmation,
): Promise<ReconciledMixedElectronicRefund> {
  return requestConfirmations(
    scope,
    request,
    activation,
    mixedElectronicRefundConfirmationSchema.parse(confirmation),
  );
}

export function readMixedElectronicRefundConfirmations(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  activation: SavedMixedElectronicRefundActivation,
): Promise<ReconciledMixedElectronicRefund> {
  return requestConfirmations(scope, request, activation);
}
