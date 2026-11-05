import { z } from "zod";

import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import {
  parseSavedElectronicRefundActivation,
  type ElectronicRefundActivationScope,
  type SavedElectronicRefundActivation,
} from "@/features/sales/electronicRefundActivation";
import {
  electronicRefundSaveRequestSchema,
  type ElectronicRefundSaveRequest,
} from "@/features/sales/electronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

const uuid = z.string().uuid();
const scopeSchema = z
  .object({ tenantId: uuid, userId: uuid, parentSaleId: uuid, branchId: uuid, registerId: uuid })
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
export const electronicRefundConfirmationSchema = z
  .object({
    original_payment_id: uuid,
    terminal_id: evidenceText(64),
    document_number: evidenceText(128),
  })
  .strict();
export type ElectronicRefundConfirmation = z.infer<typeof electronicRefundConfirmationSchema>;
const timestamp = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)));
const savedConfirmationSchema = electronicRefundConfirmationSchema
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
export type ReconciledElectronicRefund = Omit<
  z.infer<typeof reconciliationSchema>,
  "activation"
> & {
  activation: SavedElectronicRefundActivation;
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
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  activation: SavedElectronicRefundActivation,
  confirmation?: ElectronicRefundConfirmation,
): Promise<ReconciledElectronicRefund> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  const expected = parseSavedElectronicRefundActivation(activation, validatedScope, validated);
  const submitted =
    confirmation === undefined ? undefined : electronicRefundConfirmationSchema.parse(confirmation);
  const payments = new Map(
    expected.review.preview.payments
      .filter((part) => part.method !== "cash")
      .map((part) => [part.original_payment_id, part]),
  );
  if (submitted && !payments.has(submitted.original_payment_id))
    throw new Error("Подтверждение относится к другому электронному платежу.");
  const active = capturePosSessionGuard(validatedScope);
  const requireActive = (): void => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
  };
  requireActive();
  const path = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/unit-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}/confirmations`;
  const config = withAuthSession({ timeout: 8_000 });
  const response = submitted
    ? await api.post<unknown>(path, submitted, config)
    : await api.get<unknown>(path, config);
  requireActive();
  return parseElectronicRefundConfirmations(
    response.data,
    validatedScope,
    validated,
    expected,
    submitted,
  );
}

export function parseElectronicRefundConfirmations(
  data: unknown,
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  activation: SavedElectronicRefundActivation,
  submitted?: ElectronicRefundConfirmation,
): ReconciledElectronicRefund {
  const validatedScope = scopeSchema.parse(scope);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  const expected = parseSavedElectronicRefundActivation(activation, validatedScope, validated);
  const payments = new Map(
    expected.review.preview.payments
      .filter((part) => part.method !== "cash")
      .map((part) => [part.original_payment_id, part]),
  );
  const result = reconciliationSchema.parse(data);
  const marker = parseSavedElectronicRefundActivation(result.activation, validatedScope, validated);
  if (JSON.stringify(marker) !== JSON.stringify(expected))
    throw new Error("Подтверждения относятся к другому сохранённому маркеру активации.");
  const seen = new Set<string>();
  const documents = new Set<string>();
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

export async function recordElectronicRefundConfirmation(
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  activation: SavedElectronicRefundActivation,
  confirmation: ElectronicRefundConfirmation,
): Promise<ReconciledElectronicRefund> {
  return requestConfirmations(
    scope,
    request,
    activation,
    electronicRefundConfirmationSchema.parse(confirmation),
  );
}

export function readElectronicRefundConfirmations(
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  activation: SavedElectronicRefundActivation,
): Promise<ReconciledElectronicRefund> {
  return requestConfirmations(scope, request, activation);
}
