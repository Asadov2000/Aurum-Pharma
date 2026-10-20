import { z } from "zod";

import { hasPermissionForBranch } from "@/features/auth/permissions";
import { getCurrentShift } from "@/features/pos/api";
import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { useAuthStore } from "@/stores/auth";

import {
  activateElectronicRefundReview,
  readElectronicRefundActivation,
  type ElectronicRefundActivationScope,
  type SavedElectronicRefundActivation,
} from "./electronicRefundActivation";
import {
  bindElectronicRefundReview,
  cancelElectronicRefundBinding,
  readElectronicRefundBinding,
  type ElectronicRefundBinding,
} from "./electronicRefundBinding";
import {
  readElectronicRefundConfirmations,
  recordElectronicRefundConfirmation,
  type ElectronicRefundConfirmation,
  type ReconciledElectronicRefund,
} from "./electronicRefundConfirmations";
import {
  postElectronicRefund,
  readElectronicRefundPosting,
  type ElectronicRefundPostingRequest,
  type PostedElectronicRefund,
} from "./electronicRefundPosting";
import {
  electronicRefundSaveRequestSchema,
  readElectronicRefundReview,
  saveElectronicRefundReview,
  type ElectronicRefundPreview,
  type ElectronicRefundReviewScope,
  type SavedElectronicRefundReview,
} from "./electronicRefundReview";

const scopeSchema = z
  .object({
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    parentSaleId: z.string().uuid(),
    branchId: z.string().uuid(),
    registerId: z.string().uuid(),
  })
  .strict();
const pendingSchema = z
  .object({
    version: z.literal(1),
    scope: scopeSchema,
    request: electronicRefundSaveRequestSchema,
    // No receipts, costs, operator evidence or free text belong in the journal.
    attempted: z.enum(["save", "bind", "activate", "confirm", "post", "cancel"]),
  })
  .strict();
export type PendingElectronicRefund = z.infer<typeof pendingSchema>;
export interface ElectronicRefundRecovery {
  pending: PendingElectronicRefund;
  review: SavedElectronicRefundReview;
  binding?: ElectronicRefundBinding;
  activation?: SavedElectronicRefundActivation;
  evidence?: ReconciledElectronicRefund;
  posted?: PostedElectronicRefund;
}
export function electronicRefundReviewScope(
  scope: ElectronicRefundActivationScope,
): ElectronicRefundReviewScope {
  const { tenantId, userId, parentSaleId, branchId } = scope;
  return { tenantId, userId, parentSaleId, branchId };
}
export function electronicRefundKey(scope: ElectronicRefundActivationScope): string {
  const s = scopeSchema.parse(scope);
  return `sales:electronicRefund:v1:${encodeURIComponent(JSON.stringify([s.tenantId, s.userId, s.parentSaleId, s.branchId, s.registerId]))}`;
}
export function loadElectronicRefund(
  scope: ElectronicRefundActivationScope,
): PendingElectronicRefund | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(electronicRefundKey(scope));
  } catch {
    throw new Error("Хранилище недоступно. Новую операцию не отправляйте.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (
      JSON.stringify(pending.scope) !== JSON.stringify(scopeSchema.parse(scope)) ||
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
function writable(scope: ElectronicRefundActivationScope, external = false): void {
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
  scope: ElectronicRefundActivationScope,
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
  scope: ElectronicRefundActivationScope,
  action: (check: (pending: PendingElectronicRefund | null) => void) => Promise<T>,
  alive: () => boolean,
): Promise<T> {
  const s = scopeSchema.parse(scope);
  const active = capturePosSessionGuard(s);
  const check = (pending: PendingElectronicRefund | null): void => {
    if (!active() || !alive()) throw new Error("Контекст изменился. Откройте возврат заново.");
    if (!hasPermissionForBranch(useAuthStore.getState().user, "pos.refund", s.branchId))
      throw new Error("Доступ к возврату изменился.");
    if (JSON.stringify(loadElectronicRefund(s)) !== JSON.stringify(pending))
      throw new Error("Журнал изменился. Проверьте сохранённую операцию заново.");
  };
  if (!navigator.locks?.request)
    throw new Error("Для безопасного возврата нужен браузер с Web Locks.");
  return navigator.locks.request(electronicRefundKey(s), async () => {
    if (!active() || !alive()) throw new Error("Контекст изменился. Откройте возврат заново.");
    return action(check);
  });
}
async function readPending(
  scope: ElectronicRefundActivationScope,
  pending: PendingElectronicRefund,
  check: (p: PendingElectronicRefund) => void,
): Promise<ElectronicRefundRecovery> {
  const request = pending.request;
  const reviewScope = electronicRefundReviewScope(scope);
  check(pending);
  const review = await readElectronicRefundReview(reviewScope, request);
  check(pending);
  const result: ElectronicRefundRecovery = { pending, review };
  if (pending.attempted === "save") return result;
  result.binding = await readElectronicRefundBinding(reviewScope, request);
  check(pending);
  if (
    result.binding.state === "cancelled" ||
    pending.attempted === "bind" ||
    pending.attempted === "cancel"
  )
    return result;
  result.activation = await readElectronicRefundActivation(scope, request);
  check(pending);
  result.evidence = await readElectronicRefundConfirmations(scope, request, result.activation);
  check(pending);
  if (pending.attempted === "post") {
    result.posted = await readElectronicRefundPosting(scope, request, result.evidence);
    check(pending);
  }
  return result;
}
/** Reopen/reload never sends a command, including after a lost command response. */
export function inspectElectronicRefund(
  scope: ElectronicRefundActivationScope,
  alive: () => boolean = () => true,
): Promise<ElectronicRefundRecovery | null> {
  scope = scopeSchema.parse(scope);
  return exclusively(
    scope,
    async (check) => {
      const pending = loadElectronicRefund(scope);
      check(pending);
      return pending ? readPending(scope, pending, check) : null;
    },
    alive,
  );
}
export function saveElectronicRefund(
  scope: ElectronicRefundActivationScope,
  preview: ElectronicRefundPreview,
  alive: () => boolean,
): Promise<ElectronicRefundRecovery> {
  scope = scopeSchema.parse(scope);
  preview = structuredClone(preview);
  return exclusively(
    scope,
    async (check) => {
      check(null);
      await commandAuthority(scope, () => check(null));
      const pending = pendingSchema.parse({
        version: 1,
        scope,
        attempted: "save",
        request: {
          operation_id: generateUuidV4(),
          request: {
            parent_sale_id: scope.parentSaleId,
            selection: {
              items: preview.lines.map(({ line_id, sale_format, quantity }) => ({
                line_id,
                sale_format,
                quantity,
              })),
            },
            expected_total_amount: preview.total_amount,
            expected_revision_hash: preview.revision_hash,
          },
        },
      });
      if (!writeStoredJson(electronicRefundKey(scope), pending))
        throw new Error("Не удалось сохранить запрос. Команда не отправлена.");
      check(pending);
      const review = await saveElectronicRefundReview(
        electronicRefundReviewScope(scope),
        pending.request,
      );
      check(pending);
      return { pending, review };
    },
    alive,
  );
}
type Command =
  | { kind: "bind" | "cancel" }
  | { kind: "activate"; shiftId: string }
  | { kind: "confirm"; confirmation: ElectronicRefundConfirmation }
  | { kind: "post"; details: ElectronicRefundPostingRequest };
/** A cashier action executes exactly one command. Unknown command results cannot be retried. */
export function commandElectronicRefund(
  scope: ElectronicRefundActivationScope,
  expected: ElectronicRefundRecovery,
  command: Command,
  alive: () => boolean,
): Promise<ElectronicRefundRecovery> {
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
      const pending = { ...current.pending, attempted: command.kind };
      if (!writeStoredJson(electronicRefundKey(scope), pending))
        throw new Error("Не удалось сохранить этап. Команда не отправлена.");
      check(pending);
      const next = { ...current, pending };
      if (command.kind === "bind")
        next.binding = await bindElectronicRefundReview(
          electronicRefundReviewScope(scope),
          pending.request,
        );
      else if (command.kind === "cancel")
        next.binding = await cancelElectronicRefundBinding(
          electronicRefundReviewScope(scope),
          pending.request,
        );
      else if (command.kind === "activate") {
        next.activation = await activateElectronicRefundReview(
          scope,
          pending.request,
          command.shiftId,
        );
        check(pending);
        next.evidence = await readElectronicRefundConfirmations(
          scope,
          pending.request,
          next.activation,
        );
      } else if (command.kind === "confirm" && current.activation)
        next.evidence = await recordElectronicRefundConfirmation(
          scope,
          pending.request,
          current.activation,
          command.confirmation,
        );
      else if (command.kind === "post" && current.evidence)
        next.posted = await postElectronicRefund(
          scope,
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
export function acknowledgeElectronicRefund(
  scope: ElectronicRefundActivationScope,
  operationId: string,
  alive: () => boolean,
): Promise<string> {
  scope = scopeSchema.parse(scope);
  return exclusively(
    scope,
    async (check) => {
      const pending = loadElectronicRefund(scope);
      if (!pending || pending.request.operation_id !== operationId)
        throw new Error("Операция изменилась.");
      const result = await readPending(scope, pending, check);
      if (!result.posted && result.binding?.state !== "cancelled")
        throw new Error("Проведение ещё не подтверждено.");
      check(pending);
      if (!removeStoredValue(electronicRefundKey(scope)))
        throw new Error(
          "Не удалось подтвердить результат. Сохранённый возврат оставлен в журнале.",
        );
      return result.posted?.sale_id ?? scope.parentSaleId;
    },
    alive,
  );
}
