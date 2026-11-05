import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useForm, type Resolver, type ResolverResult } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { capturePosSessionGuard, currentPosStorageScope } from "@/features/pos/posStorageScope";
import { useCurrentShiftQuery } from "@/features/pos/queries";
import { type SaleDetails } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";
import { REFUND_REASON_OPTIONS } from "@/lib/refundReasons";

import {
  getMixedRefundOptions,
  mixedQuantity,
  quoteMixedRefund,
  refundCents,
  refundReasonSchema,
  validateMixedRefundQuote,
  type MixedReturnResult,
  type MixedRefundItem,
  type MixedRefundOptions,
  type MixedRefundQuote,
} from "./mixedRefund";
import {
  abandonMixedRefund,
  acknowledgeMixedRefund,
  inspectMixedRefund,
  isZeroMixedReturn,
  loadMixedRefund,
  mixedRefundKey,
  runMixedRefund,
  runMixedZeroReturn,
  type MixedRefundScope,
  type PendingMixedRefund,
} from "./mixedRefundRecovery";
import { salesKeys } from "./queries";

const formSchema = z.object({
  lines: z.array(
    z.object({
      selected: z.boolean(),
      line_id: z.string().uuid(),
      inventory_kind: z.enum(["legacy_pack", "physical_unit"]),
      sale_format: z.enum(["unit", "blister", "pack"]),
      quantity: z.string(),
    }),
  ),
  reason_code: z.union([refundReasonSchema, z.literal("")]),
  comment: z.string().max(500),
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
              message: "Проверьте количество и пояснение (не больше 500 символов).",
            },
          },
        },
      };
};
function message(reason: unknown): string {
  return reason instanceof Error && /[а-яё]/i.test(reason.message)
    ? reason.message
    : describeApiError(reason);
}
function legacyQuantity(raw: string): string {
  if (!/^(0|[1-9][0-9]{0,10})(\.[0-9]{1,3})?$/.test(raw))
    throw new Error("Укажите количество пачек с точностью до 0,001.");
  const [whole, fraction = ""] = raw.split(".");
  return `${whole}.${fraction.padEnd(3, "0")}`;
}
function initialValues(options: MixedRefundOptions): FormValues {
  return {
    lines: options.lines.map((line) => ({
      selected: false,
      line_id: line.line_id,
      inventory_kind: line.inventory_kind,
      sale_format: "unit",
      quantity:
        line.inventory_kind === "legacy_pack" ? "0.001" : String(line.packaging.dispense_step),
    })),
    reason_code: "",
    comment: "",
  };
}

export function MixedRefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleDetails;
  onClose: () => void;
  onRefunded: (saleId: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [scope] = useState<MixedRefundScope | null>(() => {
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
  const mounted = useRef(true);
  const currentSale = useRef(sale.id);
  currentSale.current = sale.id;
  const active = useCallback(
    (): boolean => mounted.current && guard() && currentSale.current === scope?.parentSaleId,
    [guard, scope],
  );
  const reload = useRef(0);
  const [options, setOptions] = useState<MixedRefundOptions | null>(null);
  const [pending, setPending] = useState<PendingMixedRefund | null>(null);
  const [completed, setCompleted] = useState<MixedReturnResult | null>(null);
  const [calculation, setCalculation] = useState<{ key: string; quote: MixedRefundQuote } | null>(
    null,
  );
  const [busy, setBusy] = useState(true);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shift = useCurrentShiftQuery(sale.register_id);
  const form = useForm<FormValues>({
    resolver: resolveForm,
    defaultValues: { lines: [], reason_code: "", comment: "" },
  });
  const { reset } = form;
  const selected = form.watch("lines").filter((line) => line.selected);
  const selectionKey = JSON.stringify(selected);
  const quote = calculation?.key === selectionKey ? calculation.quote : null;
  const locked = busy || blocked || pending !== null || !guard();
  const ownShift =
    scope &&
    shift.data?.status === "open" &&
    shift.data.opened_by_user_id === scope.userId &&
    shift.data.branch_id === scope.branchId &&
    shift.data.register_id === scope.registerId
      ? shift.data
      : null;

  const refresh = useCallback(async (): Promise<void> => {
    const version = ++reload.current;
    if (!scope || !active()) {
      setBusy(false);
      setBlocked(true);
      return;
    }
    setBusy(true);
    setError(null);
    setCalculation(null);
    setCompleted(null);
    setOptions(null);
    try {
      const stored = loadMixedRefund(scope);
      setPending(stored);
      if (stored) {
        const status = await inspectMixedRefund(scope);
        if (active() && version === reload.current && status && status.state !== "not_found")
          setCompleted(status);
      } else {
        const available = await getMixedRefundOptions(
          scope.parentSaleId,
          scope.branchId,
          scope.registerId,
          scope,
        );
        if (active() && version === reload.current) {
          setOptions(available);
          reset(initialValues(available));
        }
      }
      if (active() && version === reload.current) setBlocked(false);
    } catch (reason) {
      if (active() && version === reload.current) {
        setError(message(reason));
        setBlocked(true);
      }
    } finally {
      if (active() && version === reload.current) setBusy(false);
    }
  }, [scope, active, reset]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const changed = (event: StorageEvent): void => {
      if (scope && (event.key === mixedRefundKey(scope) || event.key === null)) void refresh();
    };
    window.addEventListener("storage", changed);
    return () => {
      mounted.current = false;
      window.removeEventListener("storage", changed);
    };
  }, [refresh, scope]);

  function selectedItems(values: FormValues): MixedRefundItem[] {
    if (!options) throw new Error("Дождитесь загрузки доступного количества.");
    const items: MixedRefundItem[] = values.lines
      .filter((line) => line.selected)
      .map((line) => {
        const original = options.lines.find((item) => item.line_id === line.line_id);
        if (!original || original.inventory_kind !== line.inventory_kind)
          throw new Error("Перезагрузите доступные товары для возврата.");
        if (original.inventory_kind === "legacy_pack") {
          const quantity = legacyQuantity(line.quantity.trim());
          if (
            mixedQuantity(quantity) <= 0n ||
            mixedQuantity(quantity) > mixedQuantity(original.remaining_quantity)
          )
            throw new Error("Количество пачек превышает доступное для возврата.");
          return { inventory_kind: "legacy_pack", line_id: line.line_id, quantity };
        }
        if (!/^[1-9][0-9]{0,10}$/.test(line.quantity.trim()))
          throw new Error("Для штук, блистеров и пачек укажите целое положительное количество.");
        const quantity = Number(line.quantity);
        const size =
          line.sale_format === "pack"
            ? original.packaging.units_per_pack
            : line.sale_format === "blister"
              ? original.packaging.units_per_blister
              : 1;
        if (
          !size ||
          BigInt(quantity) * BigInt(size) > BigInt(original.remaining_base_units) ||
          (line.sale_format === "unit" && quantity % original.packaging.dispense_step !== 0)
        )
          throw new Error("Количество не соответствует упаковке или оставшемуся товару.");
        return {
          inventory_kind: "physical_unit",
          line_id: line.line_id,
          sale_format: line.sale_format,
          quantity,
        };
      });
    if (items.length === 0) throw new Error("Выберите товары для возврата.");
    return items;
  }
  async function execute(
    action: (current: () => boolean) => Promise<void>,
    readPending = false,
  ): Promise<void> {
    if (!scope || busy || !active()) return;
    const version = reload.current;
    const current = (): boolean => active() && version === reload.current;
    setBusy(true);
    setError(null);
    try {
      await action(current);
    } catch (reason) {
      if (current()) setError(message(reason));
    } finally {
      if (current()) {
        if (readPending) {
          try {
            setPending(loadMixedRefund(scope));
          } catch (reason) {
            setBlocked(true);
            setError(message(reason));
          }
        }
        setBusy(false);
      }
    }
  }
  const calculate = form.handleSubmit(async (values) => {
    if (locked || !scope || !options) return;
    const available = options;
    await execute(async (current) => {
      const items = selectedItems(values);
      const result = await quoteMixedRefund(scope.parentSaleId, items, scope);
      validateMixedRefundQuote(result, items, available);
      if (current())
        setCalculation({
          key: JSON.stringify(values.lines.filter((line) => line.selected)),
          quote: result,
        });
    });
  });
  const submit = form.handleSubmit(async (values) => {
    if (locked || !scope || !options || !quote) return;
    const withoutPayout = refundCents(quote.total_amount) === 0n;
    if (!withoutPayout && !ownShift) return;
    if (options.reason_mode.startsWith("required") && !values.reason_code) {
      form.setError("reason_code", { message: "Выберите причину возврата" });
      return;
    }
    if (options.reason_mode === "required_with_text" && !values.comment.trim()) {
      form.setError("comment", { message: "Кратко поясните причину возврата" });
      return;
    }
    await execute(async (current) => {
      const common = {
        items: selectedItems(values),
        expected_revision_hash: quote.revision_hash,
        reason_code: options.reason_mode === "off" ? null : values.reason_code || null,
        comment: options.reason_mode === "off" ? null : values.comment.trim() || null,
      };
      const result = withoutPayout
        ? await runMixedZeroReturn(scope, common, quote)
        : await runMixedRefund(scope, {
            ...common,
            shift_id: ownShift!.id,
            expected_total_amount: quote.total_amount,
          });
      if (current()) setCompleted(result);
    }, true);
  });
  async function recover(retry: boolean): Promise<void> {
    if (!scope) return;
    await execute(async (current) => {
      const result = retry
        ? pending && isZeroMixedReturn(pending)
          ? await runMixedZeroReturn(scope)
          : await runMixedRefund(scope)
        : await inspectMixedRefund(scope);
      if (!current()) return;
      setBlocked(false);
      if (result && result.state !== "not_found") {
        setCompleted(result);
      } else
        setError(
          pending && isZeroMixedReturn(pending)
            ? "Сервер пока не подтвердил приём. Проверьте результат или повторите эту же операцию."
            : "Сервер пока не подтвердил возврат. Деньги повторно не выдавайте. Проверьте результат или повторите эту же операцию.",
        );
    }, true);
  }
  async function recalculate(): Promise<void> {
    if (!scope) return;
    await execute(async (current) => {
      const result = await abandonMixedRefund(scope);
      if (!current()) return;
      if (result.state !== "cleared") {
        setCompleted(result);
        return;
      }
      const available = await getMixedRefundOptions(
        scope.parentSaleId,
        scope.branchId,
        scope.registerId,
        scope,
      );
      if (current()) {
        setPending(null);
        setCalculation(null);
        setOptions(available);
        reset(initialValues(available));
      }
    }, true);
  }
  async function openReceipt(): Promise<void> {
    if (!scope || !completed) return;
    await execute(async (current) => {
      if (!(await acknowledgeMixedRefund(scope, completed.operation_id)))
        throw new Error("Не удалось подтвердить сохранённый результат. Проверьте возврат ещё раз.");
      if (!current()) return;
      void queryClient.invalidateQueries({ queryKey: ["sales", "list"] });
      void queryClient.invalidateQueries({ queryKey: salesKeys.detail(scope.parentSaleId) });
      if (completed.state === "prepared_quarantine") {
        onRefunded(completed.parent_sale_id);
        onClose();
      } else {
        void queryClient.invalidateQueries({ queryKey: salesKeys.detail(completed.sale_id) });
        onRefunded(completed.sale_id);
      }
    });
  }

  return (
    <Modal open onClose={onClose} title="Возврат смешанной продажи" className="max-w-3xl">
      <div className="space-y-4">
        <p className="text-sm text-foreground-muted">
          Чек № {sale.receipt_number}. Возвращённый товар поступит в карантин и не станет доступен
          для продажи.
        </p>
        {!guard() && <p role="alert">Аккаунт изменился. Откройте возврат заново.</p>}
        {error && (
          <p role="alert" className="text-sm text-danger">
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
                <p>
                  Чек возврата № {completed.receipt.receipt_number}. Повторно выдавать деньги по
                  этой операции не нужно.
                </p>
              </>
            )}
            <Button disabled={busy || !guard()} onClick={() => void openReceipt()}>
              {completed.state === "prepared_quarantine" ? "Готово" : "Открыть чек возврата"}
            </Button>
          </div>
        ) : pending ? (
          <div className="space-y-3 rounded-lg border p-4">
            <h3 className="font-semibold">Проверяем ранее отправленный возврат</h3>
            <p>
              {isZeroMixedReturn(pending)
                ? "Карантин без выплаты денег."
                : `Сумма: ${pending.request.expected_total_amount} TJS.`}{" "}
              Товары и причина сохранены. Новый возврат пока недоступен.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !guard()} onClick={() => void recover(false)}>
                Проверить результат
              </Button>
              <Button
                variant="secondary"
                disabled={busy || blocked || !guard()}
                onClick={() => void recover(true)}
              >
                Повторить эту операцию
              </Button>
              {pending.definiteRejected && (
                <Button
                  variant="secondary"
                  disabled={busy || blocked || !guard()}
                  onClick={() => void recalculate()}
                >
                  Пересчитать возврат
                </Button>
              )}
            </div>
          </div>
        ) : (
          <form
            noValidate
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
                disabled={
                  locked ||
                  (line.inventory_kind === "legacy_pack"
                    ? mixedQuantity(line.remaining_quantity) === 0n
                    : line.remaining_base_units === 0)
                }
                className="rounded-lg border p-4"
              >
                <label className="flex items-center gap-3 font-medium">
                  <input type="checkbox" {...form.register(`lines.${index}.selected`)} />
                  {line.name}
                </label>
                <p className="mt-1 text-sm text-foreground-muted">
                  Можно вернуть:{" "}
                  {line.inventory_kind === "legacy_pack"
                    ? `${line.remaining_quantity} уп.`
                    : `${line.remaining_base_units} ${line.unit_label}`}
                  .
                </p>
                {line.inventory_kind === "physical_unit" && (
                  <p className="text-sm text-foreground-muted">
                    В пачке {line.packaging.units_per_pack} {line.unit_label}
                    {line.packaging.units_per_blister
                      ? `, в блистере ${line.packaging.units_per_blister} ${line.unit_label}`
                      : ""}
                    .
                    {line.packaging.dispense_step > 1
                      ? ` Поштучный возврат кратен ${line.packaging.dispense_step}.`
                      : ""}
                  </p>
                )}
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {line.inventory_kind === "physical_unit" && (
                    <div>
                      <Label htmlFor={`mixed-refund-format-${index}`}>Что возвращают</Label>
                      <Select
                        id={`mixed-refund-format-${index}`}
                        {...form.register(`lines.${index}.sale_format`)}
                      >
                        <option value="unit">Штуки</option>
                        {line.packaging.units_per_blister && (
                          <option value="blister">Блистеры</option>
                        )}
                        <option value="pack">Пачки</option>
                      </Select>
                    </div>
                  )}
                  <div>
                    <Label htmlFor={`mixed-refund-qty-${index}`}>Количество {line.name}</Label>
                    <Input
                      id={`mixed-refund-qty-${index}`}
                      type="number"
                      inputMode="decimal"
                      min={line.inventory_kind === "legacy_pack" ? "0.001" : 1}
                      step={line.inventory_kind === "legacy_pack" ? "0.001" : 1}
                      {...form.register(`lines.${index}.quantity`)}
                    />
                  </div>
                </div>
              </fieldset>
            ))}
            {options && options.reason_mode !== "off" && (
              <fieldset disabled={locked} className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="mixed-refund-reason">
                    Причина возврата{options.reason_mode.startsWith("required") ? " *" : ""}
                  </Label>
                  <Select id="mixed-refund-reason" {...form.register("reason_code")}>
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
                  <Label htmlFor="mixed-refund-comment">
                    Пояснение{options.reason_mode === "required_with_text" ? " *" : ""}
                  </Label>
                  <Textarea
                    id="mixed-refund-comment"
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
                  К возврату наличными: <strong>{quote.total_amount} TJS</strong>
                </p>
                {refundCents(quote.total_amount) === 0n && (
                  <p>
                    Сумма по исходному чеку — 0,00 TJS. Товар будет принят в карантин без выплаты
                    денег. Открытая смена не требуется.
                  </p>
                )}
              </div>
            )}
            {!ownShift && (!quote || refundCents(quote.total_amount) > 0n) && (
              <p className="text-sm text-foreground-muted">
                Для выдачи денег откройте свою смену на кассе исходной продажи.
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="submit" variant="secondary" disabled={locked || selected.length === 0}>
                Рассчитать возврат
              </Button>
              <Button
                type="button"
                disabled={locked || !quote || (refundCents(quote.total_amount) > 0n && !ownShift)}
                onClick={() => void submit()}
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
