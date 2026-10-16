import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label } from "@/components/ui";
import { getAuthSessionGeneration } from "@/stores/auth";

import { type PosStorageScope } from "./posStorageScope";
import { type PaymentMethod } from "./types";
import {
  getActiveUnitPayment,
  getUnitPaymentIntent,
  getUnitPaymentQuote,
  normalizePaymentMoney,
  paymentCents,
  previewUnitPayment,
  saveUnitPaymentIntent,
  unitPaymentBindingAction,
  unitPaymentRequestSchema,
  type SavedUnitPaymentIntent,
  type UnitPaymentBinding,
} from "./unitPaymentPreparation";
import {
  clearUnitPaymentMarker,
  loadUnitPaymentMarker,
  storeUnitPaymentMarker,
} from "./unitPaymentPreparationStorage";

interface Props {
  saleId: string;
  scope: PosStorageScope;
  online: boolean;
  canPrepare: boolean;
  canCancel?: boolean;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  sessionIsActive: () => boolean;
  beforeStart?: () => Promise<void>;
  onBlockedChange: (blocked: boolean) => void;
}
const amount = z.string().transform((value, context) => {
  try {
    return normalizePaymentMoney(value || "0");
  } catch {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Введите сумму с точностью до дирамов.",
    });
    return z.NEVER;
  }
});
const formSchema = z.object({ cash: amount, card: amount, qr: amount, tendered: amount });
type Form = { cash: string; card: string; qr: string; tendered: string };
const emptyForm: Form = { cash: "0", card: "0", qr: "0", tendered: "0" };
const labels = { cash: "Наличными, TJS", card: "Картой, TJS", qr: "Через QR, TJS" };

/** Draft preparation only: no terminal confirmation or movement of money. */
export function PhysicalPaymentPreparationPanel(props: Props): JSX.Element {
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(false);
  const epoch = useRef(0);
  const running = useRef(false);
  const [busy, setBusy] = useState(true);
  const [opened, setOpened] = useState(false);
  const [marker, setMarker] = useState<string | null>(null);
  const [intent, setIntent] = useState<SavedUnitPaymentIntent | null>(null);
  const [binding, setBinding] = useState<UnitPaymentBinding | null>(null);
  const [total, setTotal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const form = useForm<Form>({ defaultValues: emptyForm });
  const id = useId();
  const { onBlockedChange } = props;
  const blocked = busy || error !== null || marker !== null || intent !== null || binding !== null;
  useEffect(() => {
    onBlockedChange(blocked);
  }, [blocked, onBlockedChange]);

  const run = useCallback(async (action: (check: () => void) => Promise<void>) => {
    if (running.current || !mounted.current) return;
    const generation = getAuthSessionGeneration();
    const currentEpoch = epoch.current;
    const current = () =>
      mounted.current &&
      currentEpoch === epoch.current &&
      generation === getAuthSessionGeneration() &&
      latest.current.sessionIsActive();
    const check = () => {
      if (!current()) throw new Error("Сессия изменилась. Откройте кассу заново.");
    };
    if (!current()) {
      setError("Сессия изменилась. Откройте кассу заново.");
      return;
    }
    running.current = true;
    setBusy(true);
    setError(null);
    latest.current.onBlockedChange(true);
    try {
      if (!latest.current.online)
        throw new Error("Для проверки подготовки необходимо соединение с сервером.");
      await action(check);
    } catch (cause) {
      if (current())
        setError(
          cause instanceof z.ZodError
            ? "Проверьте суммы и данные плана. Подготовка пока заблокирована."
            : cause instanceof Error
              ? cause.message
              : "Не удалось проверить подготовку. Повторите проверку результата.",
        );
    } finally {
      if (current()) {
        running.current = false;
        setBusy(false);
      }
    }
  }, []);

  const recover = useCallback(async (check: () => void) => {
    const { saleId, scope } = latest.current;
    check();
    const pending = loadUnitPaymentMarker(saleId, scope);
    setMarker(pending);
    let active = await getActiveUnitPayment(saleId, scope);
    check();
    if (pending) {
      const original = await getUnitPaymentIntent(saleId, pending, scope);
      check();
      if (original) {
        const state = await unitPaymentBindingAction(original, "binding", scope);
        check();
        if (state?.state === "cancelled") {
          clearUnitPaymentMarker(saleId, scope, pending);
          setMarker(null);
          setNotice("Подготовка отменена. Деньги и остатки не изменены.");
          active = await getActiveUnitPayment(saleId, scope);
          check();
        } else {
          setIntent(original);
          setBinding(state);
          setOpened(true);
          setTotal(original.plan.request.expected_total_amount);
          if (active.intent && active.intent.operation_id !== pending)
            setNotice(
              "В другой вкладке закреплён другой план. Сначала отмените эту незавершённую подготовку.",
            );
          return;
        }
      } else {
        if (active.intent) {
          setIntent(active.intent);
          setBinding(active.binding);
          setOpened(true);
          setTotal(active.intent.plan.request.expected_total_amount);
          setNotice(
            "В другой вкладке закреплён план. Код вашей неподтверждённой подготовки сохранён; отмените закреплённый план, чтобы продолжить проверку.",
          );
          return;
        }
        setIntent(null);
        setBinding(null);
        setOpened(true);
        setNotice(
          "Результат сохранения пока неизвестен. Проверьте результат или повторите подготовку с исходными суммами: код операции сохранён.",
        );
        return;
      }
    }
    if (active.intent) {
      storeUnitPaymentMarker(saleId, scope, active.intent.operation_id);
      setMarker(active.intent.operation_id);
      setIntent(active.intent);
      setBinding(active.binding);
      setOpened(true);
      setTotal(active.intent.plan.request.expected_total_amount);
    } else {
      setMarker(null);
      setIntent(null);
      setBinding(null);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    epoch.current += 1;
    running.current = false;
    setBusy(true);
    setOpened(false);
    setMarker(null);
    setIntent(null);
    setBinding(null);
    setError(null);
    setNotice(null);
    setTotal(null);
    void run(recover);
    return () => {
      mounted.current = false;
      epoch.current += 1;
    };
  }, [props.saleId, props.scope.tenantId, props.scope.userId, props.online, run, recover]);

  const open = () =>
    void run(async (check) => {
      if (!latest.current.canPrepare) throw new Error("Подготовка сейчас недоступна.");
      await latest.current.beforeStart?.();
      check();
      const quote = await getUnitPaymentQuote(latest.current.saleId, latest.current.scope);
      check();
      setTotal(quote.quote.total_amount);
      const method = latest.current.paymentMethods.includes("card") ? "card" : "qr";
      form.reset({ ...emptyForm, [method]: quote.quote.total_amount });
      setOpened(true);
    });

  const prepare = form.handleSubmit((fields) =>
    run(async (check) => {
      const { saleId, scope } = latest.current;
      if (!latest.current.canPrepare) throw new Error("Подготовка сейчас недоступна.");
      const pending = loadUnitPaymentMarker(saleId, scope);
      if (pending) {
        const existing = await getUnitPaymentIntent(saleId, pending, scope);
        check();
        if (existing) {
          await recover(check);
          return;
        }
      }
      const values = formSchema.parse(fields);
      await latest.current.beforeStart?.();
      check();
      const quote = await getUnitPaymentQuote(saleId, scope);
      check();
      const payments = (["cash", "card", "qr"] as const)
        .filter((method) => paymentCents(values[method]) > 0n)
        .map((method) => ({ method, amount: values[method] }));
      if (
        payments.some((part) => !latest.current.paymentMethods.includes(part.method)) ||
        (payments.length > 1 && !latest.current.mixedPaymentEnabled)
      )
        throw new Error("Выбранные способы оплаты запрещены настройками аптеки.");
      const request = unitPaymentRequestSchema.parse({
        sale_id: saleId,
        expected_revision_hash: quote.revision_hash,
        expected_total_amount: quote.quote.total_amount,
        currency: "TJS",
        payments,
        cash_tendered: paymentCents(values.cash) > 0n ? values.tendered : null,
      });
      const plan = await previewUnitPayment(request, scope);
      check();
      const operation = pending ?? crypto.randomUUID();
      storeUnitPaymentMarker(saleId, scope, operation);
      setMarker(operation);
      const saved = await saveUnitPaymentIntent(operation, plan, scope);
      check();
      setIntent(saved);
      setBinding(null);
      setTotal(saved.plan.request.expected_total_amount);
      setNotice("План сохранён. Деньги не приняты.");
    }),
  );

  const bind = () =>
    void run(async (check) => {
      if (!intent || !latest.current.canPrepare)
        throw new Error("Закрепление плана сейчас недоступно.");
      const state = await unitPaymentBindingAction(intent, "bind", latest.current.scope);
      check();
      setBinding(state);
      setNotice("План закреплён. Изменения корзины заблокированы до отмены подготовки.");
    });
  const cancel = () =>
    void run(async (check) => {
      if (!intent || !(latest.current.canCancel ?? latest.current.canPrepare))
        throw new Error("Отмена подготовки сейчас недоступна.");
      const state = await unitPaymentBindingAction(intent, "cancel-binding", latest.current.scope);
      check();
      if (state?.state !== "cancelled") throw new Error("Отмена ещё не подтверждена.");
      clearUnitPaymentMarker(latest.current.saleId, latest.current.scope, intent.operation_id);
      setIntent(null);
      setBinding(null);
      setMarker(null);
      setNotice("Подготовка отменена. Деньги и остатки не изменены.");
      await recover(check);
    });
  const disabled = busy || !props.online;
  return (
    <section aria-label="Подготовка электронной оплаты" className="space-y-3 rounded-lg border p-3">
      <p className="text-sm">Деньги не приняты. Электронная оплата ещё недоступна.</p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Проверяем подготовку…</p>}
      {!opened && !intent && (
        <Button
          type="button"
          disabled={
            disabled ||
            !props.canPrepare ||
            !props.paymentMethods.some((method) => method === "card" || method === "qr")
          }
          onClick={open}
        >
          Подготовка карты/QR
        </Button>
      )}
      {opened && !intent && (
        <form onSubmit={prepare} className="space-y-2">
          {total && <p>Итого: {total} TJS</p>}
          {!props.mixedPaymentEnabled && <p>Разрешён только один способ оплаты.</p>}
          {(["cash", "card", "qr"] as const)
            .filter(
              (method) =>
                props.paymentMethods.includes(method) &&
                (method !== "cash" || props.mixedPaymentEnabled),
            )
            .map((method) => (
              <div key={method}>
                <Label htmlFor={`${id}-${method}`}>{labels[method]}</Label>
                <Input
                  id={`${id}-${method}`}
                  inputMode="decimal"
                  {...form.register(method)}
                  disabled={disabled || !props.canPrepare}
                />
              </div>
            ))}
          {props.paymentMethods.includes("cash") && props.mixedPaymentEnabled && (
            <div>
              <Label htmlFor={`${id}-tendered`}>Получено наличными, TJS</Label>
              <Input
                id={`${id}-tendered`}
                inputMode="decimal"
                {...form.register("tendered")}
                disabled={disabled || !props.canPrepare}
              />
            </div>
          )}
          <Button type="submit" disabled={disabled || !props.canPrepare}>
            Подготовить оплату
          </Button>
        </form>
      )}
      {intent && (
        <div className="space-y-2">
          <p>Итого: {intent.plan.request.expected_total_amount} TJS</p>
          {intent.plan.request.payments.map((part) => (
            <p key={part.method}>
              {labels[part.method]}: {part.amount}
            </p>
          ))}
          <p>Сдача: {intent.plan.change_amount} TJS</p>
          {!binding && (
            <Button type="button" disabled={disabled || !props.canPrepare} onClick={bind}>
              Закрепить план
            </Button>
          )}
          {binding?.state === "bound" && <p>План закреплён. Корзина защищена от изменений.</p>}
          <Button
            type="button"
            disabled={disabled || !(props.canCancel ?? props.canPrepare)}
            onClick={cancel}
          >
            Отменить подготовку
          </Button>
        </div>
      )}
      <Button type="button" disabled={disabled} onClick={() => void run(recover)}>
        Проверить результат подготовки
      </Button>
    </section>
  );
}
