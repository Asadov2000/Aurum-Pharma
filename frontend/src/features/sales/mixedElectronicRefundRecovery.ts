import { mixedElectronicRefundGuard } from "./mixedElectronicRefundAccess";
import { z } from "zod";

import { hasPermissionForBranch } from "@/features/auth/permissions";
import { getCurrentShift } from "@/features/pos/api";
import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { useAuthStore } from "@/stores/auth";

import {
  activateMixedElectronicRefundReview,
  readMixedElectronicRefundActivation,
  type MixedElectronicRefundActivationScope,
  type SavedMixedElectronicRefundActivation,
} from "./mixedElectronicRefundActivation";
import {
  bindMixedElectronicRefundReview,
  cancelMixedElectronicRefundBinding,
  readMixedElectronicRefundBinding,
  type MixedElectronicRefundBinding,
} from "./mixedElectronicRefundBinding";
import {
  readMixedElectronicRefundConfirmations,
  recordMixedElectronicRefundConfirmation,
  type MixedElectronicRefundConfirmation,
  type ReconciledMixedElectronicRefund,
} from "./mixedElectronicRefundConfirmations";
import {
  postMixedElectronicRefund,
  readMixedElectronicRefundPosting,
  type MixedElectronicRefundPostingRequest,
  type PostedMixedElectronicRefund,
} from "./mixedElectronicRefundPosting";
import {
  mixedElectronicRefundSaveRequestSchema,
  mixedElectronicRefundSelectionSchema,
  mixedElectronicRefundPreviewSchema,
  assertMixedElectronicRefundPreview,
  readMixedElectronicRefundReview,
  saveMixedElectronicRefundReview,
  type MixedElectronicRefundPreview,
  type MixedElectronicRefundSelection,
  type MixedElectronicRefundReviewScope,
  type SavedMixedElectronicRefundReview,
} from "./mixedElectronicRefundReview";

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
const scopeSchema = z
  .object({
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    parentSaleId: z.string().uuid(),
    branchId: z.string().uuid(),
    registerId: z.string().uuid(),
  })
  .strict();
/** Fingerprint binds frozen allocations without persisting payment/receipt details. */
export async function mixedElectronicRefundPreviewHash(
  preview: MixedElectronicRefundPreview,
): Promise<string> {
  const bytes = new TextEncoder().encode(
    JSON.stringify(mixedElectronicRefundPreviewSchema.parse(preview)),
  );
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}
const pendingSchema = z
  .object({
    version: z.literal(1),
    previewHash: z.string().regex(/^[0-9a-f]{64}$/),
    activationShiftId: z.string().uuid().nullable(),
    postingShiftId: z.string().uuid().nullable(),
    scope: scopeSchema,
    request: mixedElectronicRefundSaveRequestSchema,
    // No receipts, costs, operator evidence or free text belong in the journal.
    attempted: z.enum(["save", "bind", "activate", "confirm", "post", "cancel"]),
  })
  .strict();
export type PendingMixedElectronicRefund = z.infer<typeof pendingSchema>;
export interface MixedElectronicRefundRecovery {
  pending: PendingMixedElectronicRefund;
  review: SavedMixedElectronicRefundReview;
  binding?: MixedElectronicRefundBinding;
  activation?: SavedMixedElectronicRefundActivation;
  evidence?: ReconciledMixedElectronicRefund;
  posted?: PostedMixedElectronicRefund;
}
export function mixedElectronicRefundReviewScope(
  scope: MixedElectronicRefundActivationScope,
): MixedElectronicRefundReviewScope {
  const { tenantId, userId, parentSaleId, branchId, registerId, contextCheck } = scope;
  return {
    tenantId,
    userId,
    parentSaleId,
    branchId,
    registerId,
    ...(contextCheck ? { contextCheck } : {}),
  };
}
export function mixedElectronicRefundKey(scope: MixedElectronicRefundActivationScope): string {
  const s = scopeSchema.parse(scope);
  return `sales:mixedElectronicRefund:v1:${encodeURIComponent(JSON.stringify([s.tenantId, s.userId, s.parentSaleId, s.branchId, s.registerId]))}`;
}
export function loadMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
): PendingMixedElectronicRefund | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(mixedElectronicRefundKey(scope));
  } catch {
    throw new Error("Хранилище недоступно. Новую операцию не отправляйте.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (
      canonical(pending.scope) !== canonical(scopeSchema.parse(scope)) ||
      pending.request.request.parent_sale_id !== scope.parentSaleId
    )
      throw new Error("scope");
    return pending;
  } catch {
    throw new Error(
      "Журнал возврата повреждён. Не повторяйте выплату; обратитесь к администратору.",
    );
  }
}
function writable(scope: MixedElectronicRefundActivationScope, external = false): void {
  const { user } = useAuthStore.getState();
  if (
    !user ||
    user.support_access?.is_read_only ||
    !hasPermissionForBranch(user, "pos.refund", scope.branchId) ||
    (external && !hasPermissionForBranch(user, "pos.refund_external_confirm", scope.branchId))
  )
    throw new Error(
      "Недостаточно прав для команды возврата или аптека доступна только для чтения.",
    );
}
async function commandAuthority(
  scope: MixedElectronicRefundActivationScope,
  check: () => void,
  external = false,
  expectedShift?: string,
): Promise<void> {
  check();
  writable(scope, external);
  const shift = await getCurrentShift(scope.registerId);
  check();
  writable(scope, external);
  if (
    !shift ||
    shift.status !== "open" ||
    shift.opened_by_user_id !== scope.userId ||
    shift.branch_id !== scope.branchId ||
    shift.register_id !== scope.registerId ||
    shift.currency !== "TJS" ||
    (expectedShift !== undefined && shift.id !== expectedShift)
  )
    throw new Error(
      "Нужна своя открытая смена исходной кассы. Сохранённый результат можно прочитать без неё.",
    );
}
async function exclusively<T>(
  scope: MixedElectronicRefundActivationScope,
  action: (check: (pending: PendingMixedElectronicRefund | null) => void) => Promise<T>,
  alive: () => boolean,
): Promise<T> {
  const s = scopeSchema.parse(scope);
  const active = capturePosSessionGuard(s);
  const access = mixedElectronicRefundGuard(s);
  const check = (pending: PendingMixedElectronicRefund | null): void => {
    access();
    if (!active() || !alive()) throw new Error("Контекст изменился. Откройте возврат заново.");
    if (!hasPermissionForBranch(useAuthStore.getState().user, "pos.refund", s.branchId))
      throw new Error("Доступ к возврату изменился.");
    if (canonical(loadMixedElectronicRefund(s)) !== canonical(pending))
      throw new Error("Журнал изменился. Проверьте сохранённую операцию заново.");
  };
  if (!navigator.locks?.request)
    throw new Error("Для безопасного возврата нужен браузер с Web Locks.");
  return navigator.locks.request(mixedElectronicRefundKey(s), async () => {
    access();
    if (!active() || !alive()) throw new Error("Контекст изменился. Откройте возврат заново.");
    return action(check);
  });
}
async function readPending(
  scope: MixedElectronicRefundActivationScope,
  pending: PendingMixedElectronicRefund,
  check: (p: PendingMixedElectronicRefund) => void,
): Promise<MixedElectronicRefundRecovery> {
  const request = pending.request;
  const reviewScope = mixedElectronicRefundReviewScope(scope);
  check(pending);
  const review = await readMixedElectronicRefundReview(reviewScope, request);
  check(pending);
  const digest = await mixedElectronicRefundPreviewHash(review.preview);
  check(pending);
  if (digest !== pending.previewHash) throw new Error("Сохранённый расчёт изменился.");
  const result: MixedElectronicRefundRecovery = { pending, review };
  if (pending.attempted === "save") return result;
  result.binding = await readMixedElectronicRefundBinding(reviewScope, request);
  check(pending);
  if (
    result.binding.state === "cancelled" ||
    pending.attempted === "bind" ||
    pending.attempted === "cancel"
  )
    return result;
  result.activation = await readMixedElectronicRefundActivation(scope, request);
  check(pending);
  if (result.activation.shift_id !== pending.activationShiftId)
    throw new Error("Смена активации изменилась.");
  result.evidence = await readMixedElectronicRefundConfirmations(scope, request, result.activation);
  check(pending);
  if (pending.attempted === "post") {
    result.posted = await readMixedElectronicRefundPosting(scope, request, result.evidence);
    check(pending);
    if (result.posted.shift_id !== pending.postingShiftId)
      throw new Error("Смена проведения изменилась.");
  }
  return result;
}
/** Reopen/reload never sends a command, including after a lost command response. */
export function inspectMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
  alive: () => boolean = () => true,
): Promise<MixedElectronicRefundRecovery | null> {
  scope = scopeSchema.parse(scope);
  return exclusively(
    scope,
    async (check) => {
      const pending = loadMixedElectronicRefund(scope);
      check(pending);
      return pending ? readPending(scope, pending, check) : null;
    },
    alive,
  );
}
export function saveMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
  selection: MixedElectronicRefundSelection,
  preview: MixedElectronicRefundPreview,
  alive: () => boolean,
): Promise<MixedElectronicRefundRecovery> {
  scope = scopeSchema.parse(scope);
  selection = mixedElectronicRefundSelectionSchema.parse(structuredClone(selection));
  preview = mixedElectronicRefundPreviewSchema.parse(structuredClone(preview));
  assertMixedElectronicRefundPreview(preview, scope, selection);
  if (preview.total_amount === "0.00" || !preview.payments.some((p) => p.method !== "cash"))
    throw new Error("Нужен положительный электронный возврат.");
  return exclusively(
    scope,
    async (check) => {
      check(null);
      await commandAuthority(scope, () => check(null));
      const previewHash = await mixedElectronicRefundPreviewHash(preview);
      check(null);
      const pending = pendingSchema.parse({
        version: 1,
        previewHash,
        activationShiftId: null,
        postingShiftId: null,
        scope,
        attempted: "save",
        request: {
          operation_id: generateUuidV4(),
          request: {
            parent_sale_id: scope.parentSaleId,
            selection,
            expected_total_amount: preview.total_amount,
            expected_revision_hash: preview.revision_hash,
          },
        },
      });
      if (!writeStoredJson(mixedElectronicRefundKey(scope), pending))
        throw new Error("Не удалось сохранить запрос. Команда не отправлена.");
      check(pending);
      const review = await saveMixedElectronicRefundReview(
        mixedElectronicRefundReviewScope({ ...scope, contextCheck: () => check(pending) }),
        pending.request,
      );
      check(pending);
      const digest = await mixedElectronicRefundPreviewHash(review.preview);
      check(pending);
      if (digest !== pending.previewHash) throw new Error("Сохранённый расчёт изменился.");
      return { pending, review };
    },
    alive,
  );
}
type Command =
  | { kind: "bind" | "cancel" }
  | { kind: "activate"; shiftId: string }
  | { kind: "confirm"; confirmation: MixedElectronicRefundConfirmation }
  | { kind: "post"; details: MixedElectronicRefundPostingRequest };
/** A cashier action executes exactly one command. Unknown command results cannot be retried. */
export function commandMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
  expected: MixedElectronicRefundRecovery,
  command: Command,
  alive: () => boolean,
): Promise<MixedElectronicRefundRecovery> {
  scope = scopeSchema.parse(scope);
  expected = structuredClone(expected);
  command = structuredClone(command);
  return exclusively(
    scope,
    async (check) => {
      check(expected.pending);
      writable(scope, command.kind === "confirm");
      const current = await readPending(scope, expected.pending, check);
      writable(scope, command.kind === "confirm");
      if (current.posted || current.binding?.state === "cancelled")
        throw new Error("Операция уже завершена.");
      const phase = current.pending.attempted;
      if (
        command.kind === "confirm" &&
        !current.evidence?.missing_payment_ids.includes(command.confirmation.original_payment_id)
      )
        throw new Error("Эта часть уже подтверждена или относится к другой операции.");
      if (
        (command.kind === "bind" && phase !== "save") ||
        (command.kind === "cancel" && phase !== "save" && phase !== "bind") ||
        (command.kind === "activate" && (phase !== "bind" || current.binding?.state !== "bound")) ||
        ((command.kind === "confirm" || command.kind === "post") && !current.activation) ||
        (command.kind === "post" && current.evidence?.state !== "confirmed_evidence_only")
      )
        throw new Error("Сначала проверьте предыдущий этап возврата.");
      await commandAuthority(
        scope,
        () => check(current.pending),
        command.kind === "confirm",
        command.kind === "activate"
          ? command.shiftId
          : command.kind === "post"
            ? command.details.shift_id
            : undefined,
      );
      check(current.pending);
      writable(scope, command.kind === "confirm");
      const pending = {
        ...current.pending,
        attempted: command.kind,
        activationShiftId:
          command.kind === "activate" ? command.shiftId : current.pending.activationShiftId,
        postingShiftId:
          command.kind === "post" ? command.details.shift_id : current.pending.postingShiftId,
      };
      if (!writeStoredJson(mixedElectronicRefundKey(scope), pending))
        throw new Error("Не удалось сохранить этап. Команда не отправлена.");
      check(pending);
      const next = { ...current, pending };
      const commandScope = { ...scope, contextCheck: () => check(pending) };
      if (command.kind === "bind")
        next.binding = await bindMixedElectronicRefundReview(
          mixedElectronicRefundReviewScope(commandScope),
          pending.request,
        );
      else if (command.kind === "cancel")
        next.binding = await cancelMixedElectronicRefundBinding(
          mixedElectronicRefundReviewScope(commandScope),
          pending.request,
        );
      else if (command.kind === "activate") {
        next.activation = await activateMixedElectronicRefundReview(
          commandScope,
          pending.request,
          command.shiftId,
        );
        check(pending);
        next.evidence = await readMixedElectronicRefundConfirmations(
          commandScope,
          pending.request,
          next.activation,
        );
      } else if (command.kind === "confirm" && current.activation)
        next.evidence = await recordMixedElectronicRefundConfirmation(
          commandScope,
          pending.request,
          current.activation,
          command.confirmation,
        );
      else if (command.kind === "post" && current.evidence)
        next.posted = await postMixedElectronicRefund(
          commandScope,
          pending.request,
          current.evidence,
          command.details,
        );
      check(pending);
      writable(scope, command.kind === "confirm");
      return next;
    },
    alive,
  );
}
export function acknowledgeMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
  operationId: string,
  alive: () => boolean,
): Promise<string> {
  scope = scopeSchema.parse(scope);
  return exclusively(
    scope,
    async (check) => {
      const pending = loadMixedElectronicRefund(scope);
      if (!pending || pending.request.operation_id !== operationId)
        throw new Error("Операция изменилась.");
      const result = await readPending(scope, pending, check);
      if (!result.posted && result.binding?.state !== "cancelled")
        throw new Error("Проведение ещё не подтверждено.");
      check(pending);
      if (!removeStoredValue(mixedElectronicRefundKey(scope)))
        throw new Error(
          "Не удалось подтвердить результат. Сохранённый возврат оставлен в журнале.",
        );
      return result.posted?.sale_id ?? scope.parentSaleId;
    },
    alive,
  );
}
