import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useForm, type Resolver, type ResolverResult } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { capturePosSessionGuard, currentPosStorageScope } from "@/features/pos/posStorageScope";
import { useCurrentShiftQuery } from "@/features/pos/queries";
import { type SaleDetails } from "@/features/pos/types";
import { describeApiError as describeServerError } from "@/lib/errorMessages";
import { REFUND_REASON_OPTIONS } from "@/lib/refundReasons";

import {
  getPhysicalRefundOptions,
  quotePhysicalRefund,
  refundCents,
  refundReasonSchema,
  type PhysicalReturnResult,
  type PhysicalRefundItem,
  type PhysicalRefundOptions,
  type PhysicalRefundQuote,
} from "./physicalRefund";
import {
  acknowledgePhysicalRefund,
  inspectPhysicalRefund,
  isZeroPhysicalReturn,
  loadPhysicalRefund,
  physicalRefundKey,
  runPhysicalRefund,
  runPhysicalZeroReturn,
  resetRejectedPhysicalRefund,
  type PendingPhysicalRefund,
  type PhysicalRefundScope,
} from "./physicalRefundRecovery";
import { salesKeys } from "./queries";

const formSchema = z.object({
  lines: z.array(
    z.object({
      selected: z.boolean(),
      line_id: z.string().uuid(),
      sale_format: z.enum(["unit", "blister", "pack"]),
      quantity: z.coerce.number().int().positive().safe(),
    }),
  ),
  reason_code: z.union([refundReasonSchema, z.literal("")]),
  comment: z.string().max(500, "Не больше 500 символов"),
});
type FormValues = z.infer<typeof formSchema>;
const resolveForm: Resolver<FormValues> = (values): ResolverResult<FormValues> => {
  const result = formSchema.safeParse(values);
  return result.success
    ? { values: result.data, errors: {} }
    : {
        values: {},
        errors: {
          root: {
            validation: {
              type: "validate",
              message:
                "Проверьте количество и пояснение: количество должно быть целым, пояснение — не больше 500 символов.",
            },
          },
        },
      };
};
function describeApiError(reason: unknown): string {
  if (reason instanceof Error && /[а-яё]/i.test(reason.message)) return reason.message;
  return describeServerError(reason);
}

export function PhysicalRefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleDetails;
  onClose: () => void;
  onRefunded: (saleId: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [scope] = useState<PhysicalRefundScope | null>(() => {
    const current = currentPosStorageScope();
    return current && current.tenantId === sale.tenant_id
      ? {
          ...current,
          parentSaleId: sale.id,
          branchId: sale.branch_id,
          registerId: sale.register_id,
        }
      : null;
  });
  const [guard] = useState(() => (scope ? capturePosSessionGuard(scope) : () => false));
  const mounted = useRef(true);
  const currentSale = useRef(sale);
  currentSale.current = sale;
  const active = useCallback(
    (): boolean => mounted.current && guard() && currentSale.current.id === scope?.parentSaleId,
    [guard, scope],
  );
  const [options, setOptions] = useState<PhysicalRefundOptions | null>(null);
  const [pending, setPending] = useState<PendingPhysicalRefund | null>(null);
  const [completed, setCompleted] = useState<PhysicalReturnResult | null>(null);
  const [calculation, setCalculation] = useState<{
    key: string;
    quote: PhysicalRefundQuote;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const shift = useCurrentShiftQuery(sale.register_id);
  const form = useForm<FormValues>({
    resolver: resolveForm,
    defaultValues: { lines: [], reason_code: "", comment: "" },
  });
  const { reset } = form;
  const lines = form.watch("lines");
  const selection: PhysicalRefundItem[] = lines
    .filter((line) => line.selected)
    .map((line) => ({
      line_id: line.line_id,
      sale_format: line.sale_format,
      quantity: Number(line.quantity),
    }));
  const selectionKey = JSON.stringify(selection);
  const quote = calculation?.key === selectionKey ? calculation.quote : null;
  const locked = busy || pending !== null || blocked;
  const ownOpenShift =
    scope &&
    shift.data?.status === "open" &&
    shift.data.opened_by_user_id === scope.userId &&
    shift.data.branch_id === scope.branchId &&
    shift.data.register_id === scope.registerId
      ? shift.data
      : null;

  useEffect(() => {
    mounted.current = true;
    if (!scope) {
      setBlocked(true);
      setError("Откройте возврат в аккаунте своей аптеки.");
      return;
    }
    const refresh = async (): Promise<void> => {
      try {
        const stored = loadPhysicalRefund(scope);
        if (!active()) return;
        setBlocked(false);
        setPending(stored);
        if (stored) {
          const status = await inspectPhysicalRefund(scope);
          if (active() && status && status.state !== "not_found") setCompleted(status);
          return;
        }
        const available = await getPhysicalRefundOptions(
          sale.id,
          sale.branch_id,
          sale.register_id,
          scope,
        );
        if (!active()) return;
        setOptions(available);
        reset({
          lines: available.lines.map((line) => ({
            selected: false,
            line_id: line.line_id,
            sale_format: "unit" as const,
            quantity: line.packaging.dispense_step,
          })),
          reason_code: "",
          comment: "",
        });
      } catch (reason) {
        if (active()) {
          setError(describeApiError(reason));
          setBlocked(true);
        }
      }
    };
    void refresh();
    const storageChanged = (event: StorageEvent): void => {
      if (event.key === physicalRefundKey(scope) || event.key === null) {
        setCompleted(null);
        setCalculation(null);
        void refresh();
      }
    };
    window.addEventListener("storage", storageChanged);
    return () => {
      mounted.current = false;
      window.removeEventListener("storage", storageChanged);
    };
  }, [scope, active, reset, sale.id, sale.branch_id, sale.register_id]);

  function selectedItems(values: FormValues): PhysicalRefundItem[] {
    const items = values.lines
      .filter((line) => line.selected)
      .map(({ line_id, sale_format, quantity }) => ({ line_id, sale_format, quantity }));
    if (!options || items.length === 0) throw new Error("Выберите товары для возврата.");
    for (const item of items) {
      const line = options.lines.find((candidate) => candidate.line_id === item.line_id);
      const size =
        item.sale_format === "pack"
          ? line?.packaging.units_per_pack
          : item.sale_format === "blister"
            ? line?.packaging.units_per_blister
            : 1;
      if (
        !line ||
        !size ||
        item.quantity * size > line.remaining_base_units ||
        (item.sale_format === "unit" && item.quantity % line.packaging.dispense_step !== 0)
      )
        throw new Error(
          "Проверьте количество: оно должно соответствовать упаковке и оставшемуся товару.",
        );
    }
    return items;
  }
  const calculate = form.handleSubmit(async (values) => {
    if (!scope || locked) return;
    setBusy(true);
    setError(null);
    try {
      const items = selectedItems(values);
      const result = await quotePhysicalRefund(scope.parentSaleId, items, scope);
      if (active()) setCalculation({ key: JSON.stringify(items), quote: result });
    } catch (reason) {
      if (active()) setError(describeApiError(reason));
    } finally {
      if (active()) setBusy(false);
    }
  });
  const submitRefund = form.handleSubmit(async (values) => {
    if (!scope || locked || !quote || !options) return;
    const zeroReturn = refundCents(quote.total_amount) === 0n;
    if (!zeroReturn && !ownOpenShift) return;
    if (
      (options.reason_mode === "required" || options.reason_mode === "required_with_text") &&
      !values.reason_code
    ) {
      form.setError("reason_code", { message: "Выберите причину возврата" });
      return;
    }
    if (options.reason_mode === "required_with_text" && !values.comment.trim()) {
      form.setError("comment", { message: "Кратко поясните причину возврата" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const input = {
        items: selectedItems(values),
        expected_revision_hash: quote.revision_hash,
        reason_code: options.reason_mode === "off" ? null : values.reason_code || null,
        comment: options.reason_mode === "off" ? null : values.comment.trim() || null,
      };
      const result = zeroReturn
        ? await runPhysicalZeroReturn(scope, input, quote)
        : await runPhysicalRefund(scope, {
            ...input,
            shift_id: ownOpenShift!.id,
            expected_total_amount: quote.total_amount,
          });
      if (active()) setCompleted(result);
    } catch (reason) {
      if (active()) setError(describeApiError(reason));
    } finally {
      if (active()) {
        try {
          setPending(loadPhysicalRefund(scope));
        } catch (reason) {
          setBlocked(true);
          setError(describeApiError(reason));
        }
        setBusy(false);
      }
    }
  });
  async function recover(retry: boolean): Promise<void> {
    if (!scope || busy || !active()) return;
    setBusy(true);
    setError(null);
    try {
      const result = retry
        ? pending && isZeroPhysicalReturn(pending)
          ? await runPhysicalZeroReturn(scope)
          : await runPhysicalRefund(scope)
        : await inspectPhysicalRefund(scope);
      if (active()) {
        setBlocked(false);
        if (result && result.state !== "not_found") setCompleted(result);
        else
          setError(
            pending && isZeroPhysicalReturn(pending)
              ? "Сервер пока не подтвердил карантин. Новую операцию не создавайте. Можно проверить ещё раз или повторить ту же операцию."
              : "Сервер пока не подтвердил возврат. Деньги повторно не выдавайте. Можно проверить ещё раз или повторить ту же операцию.",
          );
      }
    } catch (reason) {
      if (active()) setError(describeApiError(reason));
    } finally {
      if (active()) {
        try {
          setPending(loadPhysicalRefund(scope));
        } catch (reason) {
          setBlocked(true);
          setError(describeApiError(reason));
        }
        setBusy(false);
      }
    }
  }
  async function recalculateRejected(): Promise<void> {
    if (!scope || busy || !active()) return;
    setBusy(true);
    setError(null);
    try {
      const status = await resetRejectedPhysicalRefund(scope);
      if (!active()) return;
      if (status.state !== "cleared") {
        setCompleted(status);
        return;
      }
      setPending(null);
      setCalculation(null);
      setOptions(null);
      const available = await getPhysicalRefundOptions(
        scope.parentSaleId,
        scope.branchId,
        scope.registerId,
        scope,
      );
      if (!active()) return;
      setOptions(available);
      reset({
        lines: available.lines.map((line) => ({
          selected: false,
          line_id: line.line_id,
          sale_format: "unit" as const,
          quantity: line.packaging.dispense_step,
        })),
        reason_code: "",
        comment: "",
      });
    } catch (reason) {
      if (active()) setError(describeApiError(reason));
    } finally {
      if (active()) setBusy(false);
    }
  }
  async function openCompleted(): Promise<void> {
    if (!scope || !completed || busy || !active()) return;
    setBusy(true);
    setError(null);
    try {
      const acknowledged = await acknowledgePhysicalRefund(scope, completed.operation_id);
      if (active()) {
        if (!acknowledged)
          throw new Error(
            "Не удалось подтвердить сохранённый результат. Проверьте возврат ещё раз.",
          );
        void queryClient.invalidateQueries({ queryKey: ["sales", "list"] });
        void queryClient.invalidateQueries({ queryKey: salesKeys.detail(scope.parentSaleId) });
        if (completed.state === "prepared_quarantine") {
          onRefunded(completed.parent_sale_id);
          onClose();
        } else {
          void queryClient.invalidateQueries({ queryKey: salesKeys.detail(completed.sale_id) });
          onRefunded(completed.sale_id);
        }
      }
    } catch (reason) {
      if (active()) setError(describeApiError(reason));
    } finally {
      if (active()) setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="Возврат лекарства" className="max-w-3xl">
      <div className="space-y-5">
        <p className="text-sm text-foreground-muted">
          Чек № {sale.receipt_number}. Возвращённый товар будет помещён в карантин и не появится в
          продаже.
        </p>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger"
          >
            {error}
          </p>
        )}
        {form.formState.errors.root && (
          <p role="alert" className="text-sm text-danger">
            {form.formState.errors.root.validation?.message}
          </p>
        )}
        {completed ? (
          <div className="space-y-3 rounded-lg border p-4">
            <h3 className="font-semibold">
              {completed.state === "prepared_quarantine"
                ? "Товар принят в карантин"
                : "Возврат оформлен"}
            </h3>
            {completed.state === "prepared_quarantine" ? (
              <p>Товар помещён в карантин без выплаты денег. Он не доступен для продажи.</p>
            ) : (
              <>
                <p>
                  К выдаче наличными: <strong>{completed.total_amount} TJS</strong>.
                </p>
                <p className="text-sm text-foreground-muted">
                  Чек возврата № {completed.receipt.receipt_number}. Повторно выдавать деньги по
                  этой операции не нужно.
                </p>
              </>
            )}
            <Button disabled={busy} onClick={() => void openCompleted()}>
              {completed.state === "prepared_quarantine" ? "Готово" : "Открыть чек возврата"}
            </Button>
          </div>
        ) : pending ? (
          <div className="space-y-3 rounded-lg border p-4">
            <h3 className="font-semibold">Проверяем ранее отправленный возврат</h3>
            <p className="text-sm">
              {isZeroPhysicalReturn(pending)
                ? "Карантин без выплаты денег."
                : `Сумма: ${pending.request.expected_total_amount} TJS.`}{" "}
              Товары и причина сохранены. Новый возврат пока недоступен.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy} onClick={() => void recover(false)}>
                Проверить результат
              </Button>
              <Button
                variant="secondary"
                disabled={busy || blocked}
                onClick={() => void recover(true)}
              >
                Повторить эту операцию
              </Button>
              {pending.definiteRejected && (
                <Button
                  variant="secondary"
                  disabled={busy || blocked}
                  onClick={() => void recalculateRejected()}
                >
                  Пересчитать возврат
                </Button>
              )}
            </div>
          </div>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void calculate();
            }}
            className="space-y-4"
          >
            {!options && !blocked && <p role="status">Загружаем доступные товары…</p>}
            {options?.lines.map((line, index) => (
              <fieldset
                key={line.line_id}
                disabled={locked || line.remaining_base_units === 0}
                className="rounded-lg border p-4"
              >
                <label className="flex items-center gap-3 font-medium">
                  <input type="checkbox" {...form.register(`lines.${index}.selected`)} />
                  {line.name}
                </label>
                <p className="mt-1 text-sm text-foreground-muted">
                  Можно вернуть: {line.remaining_base_units} {line.unit_label}. В упаковке{" "}
                  {line.packaging.units_per_pack} {line.unit_label}
                  {line.packaging.units_per_blister
                    ? `, в блистере ${line.packaging.units_per_blister}`
                    : ""}
                  .
                </p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label htmlFor={`refund-format-${index}`}>Что возвращают</Label>
                    <Select
                      id={`refund-format-${index}`}
                      {...form.register(`lines.${index}.sale_format`)}
                    >
                      <option value="unit">Поштучно</option>
                      {line.packaging.units_per_blister && (
                        <option value="blister">Блистеры</option>
                      )}
                      <option value="pack">Целые упаковки</option>
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor={`refund-qty-${index}`}>Количество</Label>
                    <Input
                      id={`refund-qty-${index}`}
                      type="number"
                      min={1}
                      step={1}
                      {...form.register(`lines.${index}.quantity`)}
                      invalid={!!form.formState.errors.lines?.[index]?.quantity}
                    />
                    {form.formState.errors.lines?.[index]?.quantity && (
                      <p role="alert" className="text-sm text-danger">
                        Укажите целое положительное количество
                      </p>
                    )}
                  </div>
                </div>
              </fieldset>
            ))}
            {options && options.reason_mode !== "off" && (
              <fieldset disabled={locked} className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="physical-refund-reason">
                    Причина возврата{options.reason_mode.startsWith("required") ? " *" : ""}
                  </Label>
                  <Select
                    id="physical-refund-reason"
                    {...form.register("reason_code")}
                    invalid={!!form.formState.errors.reason_code}
                  >
                    <option value="">Выберите причину</option>
                    {REFUND_REASON_OPTIONS.map((reason) => (
                      <option key={reason.value} value={reason.value}>
                        {reason.label}
                      </option>
                    ))}
                  </Select>
                  {form.formState.errors.reason_code && (
                    <p role="alert" className="text-sm text-danger">
                      {form.formState.errors.reason_code.message}
                    </p>
                  )}
                </div>
                <div>
                  <Label htmlFor="physical-refund-comment">
                    Пояснение{options.reason_mode === "required_with_text" ? " *" : ""}
                  </Label>
                  <Textarea
                    id="physical-refund-comment"
                    rows={2}
                    maxLength={500}
                    {...form.register("comment")}
                  />
                  {form.formState.errors.comment && (
                    <p role="alert" className="text-sm text-danger">
                      {form.formState.errors.comment.message}
                    </p>
                  )}
                </div>
              </fieldset>
            )}
            {quote && (
              <div className="rounded-lg bg-surface-raised p-4">
                <p>
                  К возврату наличными:{" "}
                  <strong className="text-lg">{quote.total_amount} TJS</strong>
                </p>
                {refundCents(quote.total_amount) === 0n && (
                  <p className="mt-2 text-sm">
                    По исходному чеку сумма возврата за выбранное количество — 0,00 TJS. Примите
                    товар в карантин без выплаты.
                  </p>
                )}
              </div>
            )}
            {!ownOpenShift && (!quote || refundCents(quote.total_amount) > 0n) && (
              <p className="text-sm text-foreground-muted">
                Для выдачи денег откройте свою смену на кассе исходной продажи.
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="submit" variant="secondary" disabled={locked || selection.length === 0}>
                Рассчитать возврат
              </Button>
              <Button
                type="button"
                disabled={
                  locked || !quote || (refundCents(quote.total_amount) > 0n && !ownOpenShift)
                }
                onClick={() => void submitRefund()}
              >
                {quote && refundCents(quote.total_amount) === 0n
                  ? "Принять в карантин без выплаты"
                  : "Оформить возврат наличными"}
              </Button>
            </div>
          </form>
        )}
        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </div>
    </Modal>
  );
}
