import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useForm, type Resolver, type ResolverResult } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/permissions";
import { capturePosSessionGuard, currentPosStorageScope } from "@/features/pos/posStorageScope";
import { useCurrentShiftQuery } from "@/features/pos/queries";
import { type SaleDetails } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";
import { REFUND_REASON_OPTIONS } from "@/lib/refundReasons";
import { useAuthStore } from "@/stores/auth";

import { type ElectronicRefundActivationScope } from "./electronicRefundActivation";
import { electronicRefundConfirmationSchema } from "./electronicRefundConfirmations";
import { electronicRefundPostingRequestSchema } from "./electronicRefundPosting";
import {
  quoteElectronicRefundReview,
  type ElectronicRefundPreview,
} from "./electronicRefundReview";
import {
  acknowledgeElectronicRefund,
  commandElectronicRefund,
  electronicRefundKey,
  electronicRefundReviewScope,
  inspectElectronicRefund,
  loadElectronicRefund,
  saveElectronicRefund,
  type ElectronicRefundRecovery,
} from "./electronicRefundRecovery";
import {
  getPhysicalRefundOptions,
  refundReasonSchema,
  type PhysicalRefundOptions,
} from "./physicalRefund";

const schema = z.object({
  lines: z.array(
    z.object({
      selected: z.boolean(),
      line_id: z.string().uuid(),
      sale_format: z.enum(["unit", "blister", "pack"]),
      quantity: z.coerce.number().int().positive().safe().max(99_999_999_999),
    }),
  ),
  reason_code: z.union([refundReasonSchema, z.literal("")]),
  comment: z.string().refine((v) => Array.from(v).length <= 500),
  confirmations: z.array(
    z.object({
      original_payment_id: z.string().uuid(),
      terminal_id: z.string(),
      document_number: z.string(),
    }),
  ),
});
type Values = z.infer<typeof schema>;
const resolver: Resolver<Values> = (values): ResolverResult<Values> => {
  const result = schema.safeParse(values);
  return result.success
    ? { values: result.data, errors: {} }
    : {
        values: {},
        errors: {
          root: {
            validation: {
              type: "validate",
              message: "Проверьте целое количество, причину и пояснение (до 500 символов).",
            },
          },
        },
      };
};
export function ElectronicRefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleDetails;
  onClose: () => void;
  onRefunded: (saleId: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [scope] = useState<ElectronicRefundActivationScope | null>(() => {
    const current = currentPosStorageScope();
    return current?.tenantId === sale.tenant_id
      ? {
          ...current,
          parentSaleId: sale.id,
          branchId: sale.branch_id,
          registerId: sale.register_id,
        }
      : null;
  });
  const [guard] = useState(() => (scope ? capturePosSessionGuard(scope) : () => false));
  const generation = useRef(0);
  const mounted = useRef(false);
  const currentSale = useRef(sale);
  currentSale.current = sale;
  const active = useCallback(
    () =>
      mounted.current &&
      guard() &&
      currentSale.current.id === scope?.parentSaleId &&
      currentSale.current.tenant_id === scope.tenantId &&
      currentSale.current.branch_id === scope.branchId &&
      currentSale.current.register_id === scope.registerId &&
      hasPermissionForBranch(useAuthStore.getState().user, "pos.refund", scope.branchId),
    [guard, scope],
  );
  const [options, setOptions] = useState<PhysicalRefundOptions | null>(null);
  const [recovery, setRecovery] = useState<ElectronicRefundRecovery | null>(null);
  const [calculation, setCalculation] = useState<{
    key: string;
    preview: ElectronicRefundPreview;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const shift = useCurrentShiftQuery(sale.register_id);
  const form = useForm<Values>({
    resolver,
    defaultValues: { lines: [], reason_code: "", comment: "", confirmations: [] },
  });
  const { reset, setValue } = form;
  const values = form.watch();
  const selected = values.lines
    .filter((l) => l.selected)
    .map(({ line_id, sale_format, quantity }) => ({
      line_id,
      sale_format,
      quantity: Number(quantity),
    }));
  const selectionKey = JSON.stringify(selected);
  const preview = calculation?.key === selectionKey ? calculation.preview : null;
  const ownShift =
    scope &&
    shift.data?.status === "open" &&
    shift.data.opened_by_user_id === scope.userId &&
    shift.data.branch_id === scope.branchId &&
    shift.data.register_id === scope.registerId &&
    shift.data.currency === "TJS"
      ? shift.data
      : null;
  const canWrite =
    !!ownShift &&
    active() &&
    !user?.support_access?.is_read_only &&
    hasPermissionForBranch(user, "pos.refund", sale.branch_id);
  const canConfirm =
    canWrite && hasPermissionForBranch(user, "pos.refund_external_confirm", sale.branch_id);
  const locked = busy || blocked || !!recovery;
  const fillConfirmations = useCallback(
    (result: ElectronicRefundRecovery) => {
      setValue(
        "confirmations",
        result.review.preview.payments
          .filter((p) => p.method !== "cash")
          .map((p) => ({
            original_payment_id: p.original_payment_id,
            terminal_id: "",
            document_number: "",
          })),
      );
    },
    [setValue],
  );
  const refresh = useCallback(async () => {
    if (!scope) return;
    const revision = ++generation.current;
    const alive = () => active() && generation.current === revision;
    setBusy(true);
    setBlocked(true);
    setError(null);
    setRecovery(null);
    setCalculation(null);
    try {
      const pending = loadElectronicRefund(scope);
      if (pending) {
        const result = await inspectElectronicRefund(scope, alive);
        if (!alive()) return;
        setRecovery(result);
        if (result) fillConfirmations(result);
        if (result && !result.posted && result.binding?.state !== "cancelled") {
          const available = await getPhysicalRefundOptions(
            sale.id,
            sale.branch_id,
            sale.register_id,
            scope,
          );
          if (!alive()) return;
          setOptions(available);
        }
      } else {
        const available = await getPhysicalRefundOptions(
          sale.id,
          sale.branch_id,
          sale.register_id,
          scope,
        );
        if (!alive()) return;
        setOptions(available);
        reset({
          lines: available.lines.map((l) => ({
            selected: false,
            line_id: l.line_id,
            sale_format: "unit",
            quantity: l.packaging.dispense_step,
          })),
          reason_code: "",
          comment: "",
          confirmations: [],
        });
      }
      if (alive()) setBlocked(false);
    } catch (reason) {
      if (alive())
        setError(
          reason instanceof Error && /[а-яё]/i.test(reason.message)
            ? reason.message
            : describeApiError(reason),
        );
    } finally {
      if (alive()) setBusy(false);
    }
  }, [scope, active, sale.id, sale.branch_id, sale.register_id, reset, fillConfirmations]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const changed = (event: StorageEvent) => {
      if (scope && (event.key === null || event.key === electronicRefundKey(scope))) void refresh();
    };
    window.addEventListener("storage", changed);
    return () => {
      mounted.current = false;
      generation.current = -1;
      window.removeEventListener("storage", changed);
    };
  }, [refresh, scope]);
  async function perform(action: (alive: () => boolean) => Promise<void>): Promise<void> {
    if (busy || !active()) return;
    const revision = generation.current;
    const alive = () => active() && generation.current === revision;
    setBusy(true);
    setError(null);
    try {
      await action(alive);
    } catch (reason) {
      if (alive()) {
        setBlocked(true);
        setRecovery(null);
        setError(
          reason instanceof Error && /[а-яё]/i.test(reason.message)
            ? reason.message
            : describeApiError(reason),
        );
      }
    } finally {
      if (alive()) setBusy(false);
    }
  }
  const calculate = form.handleSubmit(async (submitted) => {
    if (!scope || locked || !canWrite || !options) return;
    await perform(async (alive) => {
      const items = submitted.lines
        .filter((l) => l.selected)
        .map(({ line_id, sale_format, quantity }) => ({ line_id, sale_format, quantity }));
      if (!items.length) throw new Error("Выберите товары для возврата.");
      for (const item of items) {
        const line = options.lines.find((l) => l.line_id === item.line_id);
        const size =
          item.sale_format === "pack"
            ? line?.packaging.units_per_pack
            : item.sale_format === "blister"
              ? line?.packaging.units_per_blister
              : 1;
        if (
          !line ||
          !size ||
          BigInt(item.quantity) * BigInt(size) > BigInt(line.remaining_base_units) ||
          (BigInt(item.quantity) * BigInt(size)) % BigInt(line.packaging.dispense_step) !== 0n
        )
          throw new Error("Количество не соответствует исторической упаковке, шагу или остатку.");
      }
      const result = await quoteElectronicRefundReview(electronicRefundReviewScope(scope), {
        items,
      });
      if (alive()) setCalculation({ key: JSON.stringify(items), preview: result });
    });
  });
  function command(command: Parameters<typeof commandElectronicRefund>[2]): void {
    if (!scope || !recovery || blocked || !canWrite) return;
    void perform(async (alive) => {
      const result = await commandElectronicRefund(scope, recovery, command, alive);
      if (alive()) {
        setRecovery(result);
        fillConfirmations(result);
      }
    });
  }
  const post = form.handleSubmit(async (submitted) => {
    if (!ownShift || !options) return;
    if (
      (options.reason_mode === "required" || options.reason_mode === "required_with_text") &&
      !submitted.reason_code
    ) {
      form.setError("reason_code", { message: "Выберите причину возврата" });
      return;
    }
    if (options.reason_mode === "required_with_text" && !submitted.comment.trim()) {
      form.setError("comment", { message: "Поясните причину возврата" });
      return;
    }
    command({
      kind: "post",
      details: electronicRefundPostingRequestSchema.parse({
        shift_id: ownShift.id,
        reason_code: options.reason_mode === "off" ? null : submitted.reason_code || null,
        comment: options.reason_mode === "off" ? null : submitted.comment.trim() || null,
      }),
    });
  });
  return (
    <Modal open title="Электронный физический возврат" onClose={onClose} className="max-w-3xl">
      <div className="space-y-4">
        <p>
          Поддержку чека и сумму проверяет сервер. Aurum не обращается к банку и не повторяет
          внешнюю выплату.
        </p>
        {!active() && <p role="alert">Доступ изменился. Закройте форму и откройте её заново.</p>}
        {error && (
          <p role="alert">{error} Сохранённый UUID не удалён. Не повторяйте внешнюю выплату.</p>
        )}
        {form.formState.errors.root?.validation?.message && (
          <p role="alert">{form.formState.errors.root.validation?.message}</p>
        )}
        <Button variant="secondary" disabled={busy || !active()} onClick={() => void refresh()}>
          Проверить сохранённый результат
        </Button>
        {!recovery && options && (
          <form
            onSubmit={(e) => {
              void calculate(e);
            }}
            className="space-y-3"
          >
            {options.lines.map((line, i) => (
              <fieldset
                key={line.line_id}
                disabled={locked || !canWrite}
                className="space-y-2 border p-3"
              >
                <Label>
                  <input type="checkbox" {...form.register(`lines.${i}.selected`)} /> {line.name}{" "}
                  (осталось {line.remaining_base_units} {line.unit_label})
                </Label>
                <Label>
                  Формат
                  <Select
                    aria-label={`Формат: ${line.name}`}
                    {...form.register(`lines.${i}.sale_format`)}
                  >
                    <option value="unit">Штуки</option>
                    {line.packaging.units_per_blister && (
                      <option value="blister">Блистер ({line.packaging.units_per_blister})</option>
                    )}
                    <option value="pack">Упаковка ({line.packaging.units_per_pack})</option>
                  </Select>
                </Label>
                <Label>
                  Количество
                  <Input
                    aria-label={`Количество: ${line.name}`}
                    type="number"
                    min={1}
                    step={1}
                    {...form.register(`lines.${i}.quantity`, { valueAsNumber: true })}
                  />
                </Label>
                <p>
                  Шаг: {line.packaging.dispense_step} {line.unit_label}
                </p>
              </fieldset>
            ))}
            <Button type="submit" disabled={locked || !canWrite}>
              Рассчитать электронный возврат
            </Button>
          </form>
        )}
        {preview && !recovery && (
          <>
            <p>Расчёт сервера: {preview.total_amount} TJS</p>
            <Button
              disabled={locked || !canWrite}
              onClick={() => {
                if (scope)
                  void perform(async (alive) => {
                    const result = await saveElectronicRefund(scope, preview, alive);
                    if (alive()) {
                      setRecovery(result);
                      fillConfirmations(result);
                    }
                  });
              }}
            >
              Сохранить выбранный расчёт
            </Button>
          </>
        )}
        {recovery && (
          <>
            <p>Операция: {recovery.pending.request.operation_id}</p>
            <p>Возврат: {recovery.review.preview.total_amount} TJS</p>
            <ul>
              {recovery.review.preview.payments.map((p) => (
                <li key={p.original_payment_id}>
                  {p.method}: {p.amount} TJS
                </li>
              ))}
            </ul>
            {!recovery.binding && (
              <Button disabled={busy || !canWrite} onClick={() => command({ kind: "bind" })}>
                Закрепить расчёт
              </Button>
            )}
            {recovery.binding?.state !== "cancelled" &&
              !recovery.activation &&
              (recovery.pending.attempted === "save" || recovery.pending.attempted === "bind") && (
                <Button
                  variant="secondary"
                  disabled={busy || !canWrite}
                  onClick={() => command({ kind: "cancel" })}
                >
                  Отменить подготовку
                </Button>
              )}
            {recovery.binding?.state === "bound" && !recovery.activation && (
              <Button
                disabled={busy || !canWrite || !ownShift}
                onClick={() => {
                  if (ownShift) command({ kind: "activate", shiftId: ownShift.id });
                }}
              >
                Активировать в своей смене
              </Button>
            )}
            {recovery.activation && !recovery.posted && (
              <>
                <p>
                  Реквизиты вводит оператор после проверки внешнего результата. Это не ответ банка и
                  ещё не финансовое проведение.
                </p>
                {values.confirmations.map((part, i) =>
                  recovery.evidence?.missing_payment_ids.includes(part.original_payment_id) ? (
                    <fieldset key={part.original_payment_id} disabled={busy || !canConfirm}>
                      <legend>
                        Реквизиты{" "}
                        {
                          recovery.review.preview.payments.find(
                            (p) => p.original_payment_id === part.original_payment_id,
                          )?.method
                        }
                      </legend>
                      <Label>
                        Терминал
                        <Input
                          aria-label={`Терминал ${i + 1}`}
                          {...form.register(`confirmations.${i}.terminal_id`)}
                        />
                      </Label>
                      <Label>
                        Документ
                        <Input
                          aria-label={`Документ ${i + 1}`}
                          {...form.register(`confirmations.${i}.document_number`)}
                        />
                      </Label>
                      <Button
                        onClick={() => {
                          const parsed = electronicRefundConfirmationSchema.safeParse(part);
                          if (parsed.success)
                            command({ kind: "confirm", confirmation: parsed.data });
                          else
                            setError("Укажите корректные терминал и документ для выбранной части.");
                        }}
                      >
                        Сохранить ручные реквизиты
                      </Button>
                    </fieldset>
                  ) : (
                    <p key={part.original_payment_id}>Ручные реквизиты части сохранены.</p>
                  ),
                )}
              </>
            )}
            {recovery.evidence?.state === "confirmed_evidence_only" && !recovery.posted && (
              <form
                onSubmit={(e) => {
                  void post(e);
                }}
              >
                <p>Все ручные реквизиты сохранены. Для проведения подтвердите действие отдельно.</p>
                <Label>
                  Причина
                  <Select {...form.register("reason_code")}>
                    <option value="">Не указана</option>
                    {REFUND_REASON_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                </Label>
                {form.formState.errors.reason_code && (
                  <p role="alert">{form.formState.errors.reason_code.message}</p>
                )}
                <Label>
                  Пояснение
                  <Textarea {...form.register("comment")} />
                </Label>
                {form.formState.errors.comment && (
                  <p role="alert">{form.formState.errors.comment.message}</p>
                )}
                <Button type="submit" disabled={busy || !canWrite || !ownShift || !options}>
                  Провести возврат
                </Button>
              </form>
            )}
            {(recovery.posted || recovery.binding?.state === "cancelled") && (
              <>
                <p>
                  {recovery.posted
                    ? "Возврат проведён. Сохранённый чек проверен."
                    : "Подготовка окончательно отменена."}
                </p>
                <Button
                  disabled={busy || !active()}
                  onClick={() => {
                    if (scope)
                      void perform(async (alive) => {
                        const saleId = await acknowledgeElectronicRefund(
                          scope,
                          recovery.pending.request.operation_id,
                          alive,
                        );
                        if (alive()) {
                          void queryClient.invalidateQueries({ queryKey: ["sales"] });
                          void queryClient.invalidateQueries({ queryKey: ["pos", "shift"] });
                          onRefunded(saleId);
                        }
                      });
                  }}
                >
                  {recovery.posted ? "Открыть сохранённый чек возврата" : "Подтвердить отмену"}
                </Button>
              </>
            )}
          </>
        )}
        {!ownShift && (
          <p>
            Новые команды активации и проведения требуют своей открытой смены исходной кассы.
            Сохранённый результат можно читать без неё.
          </p>
        )}
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>
    </Modal>
  );
}
