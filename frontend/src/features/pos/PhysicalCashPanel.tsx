import { isAxiosError } from "axios";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { getPhysicalCartQuote, type PhysicalCartItem } from "./physicalCart";
import {
  getPhysicalCashOperation,
  parsePhysicalCashQuote,
  type PhysicalCashCheckoutResult,
} from "./physicalCashCheckout";
import {
  acknowledgePhysicalCash,
  loadPendingPhysicalCash,
  runPhysicalCash,
  type PendingPhysicalCash,
} from "./physicalCashRecovery";
import { type PosStorageScope } from "./posStorageScope";

interface Props {
  saleId: string;
  branchId: string;
  registerId: string;
  scope: PosStorageScope;
  items: PhysicalCartItem[];
  online: boolean;
  canPay: boolean;
  sessionIsActive: () => boolean;
  onBlockedChange: (blocked: boolean) => void;
  onCompleted: (result: PhysicalCashCheckoutResult, openCashDrawer: boolean) => Promise<boolean>;
}

const receivedSchema = z
  .string()
  .trim()
  .regex(/^\d{1,12}([.,]\d{1,2})?$/);
const formSchema = z.object({ received: receivedSchema });
const cents = (value: string): bigint => BigInt(value.replace(".", ""));
function normalizedMoney(value: string): string {
  const [integer = "0", fraction = ""] = value.trim().replace(",", ".").split(".");
  return `${BigInt(integer).toString()}.${fraction.padEnd(2, "0")}`;
}
function displayMoney(value: bigint): string {
  return `${value / 100n}.${(value % 100n).toString().padStart(2, "0")}`;
}
function message(error: unknown): string {
  const fallback = "Не удалось проверить оплату. Не принимайте деньги повторно.";
  if (isAxiosError(error)) return describeApiError(error, fallback);
  if (error instanceof z.ZodError)
    return "Не удалось проверить данные оплаты. Повторите проверку этой же операции.";
  return error instanceof Error ? error.message : fallback;
}

/** Financial recovery remains available even when the sale or shift has already closed. */
export function PhysicalCashPanel(props: Props): JSX.Element {
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(false);
  const busyRef = useRef(false);
  const busyGeneration = useRef(0);
  const accepted = useRef(false);
  const [initial] = useState(() => {
    try {
      return { pending: loadPendingPhysicalCash(props.saleId, props.scope), error: null };
    } catch (error) {
      return { pending: null, error: message(error) };
    }
  });
  const pendingRef = useRef(initial.pending);
  const [pending, setPending] = useState(initial.pending);
  const [error, setError] = useState<string | null>(initial.error);
  const [busy, setBusy] = useState(false);
  const [quote, setQuote] = useState<Awaited<ReturnType<typeof parsePhysicalCashQuote>> | null>(
    null,
  );
  const quoteItemsKey = useRef<string | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [finished, setFinished] = useState(false);
  const id = useId();
  const form = useForm<{ received: string }>({ defaultValues: { received: "" } });
  const received = form.watch("received");
  const active = useCallback(() => mounted.current && latest.current.sessionIsActive(), []);
  const retain = useCallback((): PendingPhysicalCash | null => {
    const stored = loadPendingPhysicalCash(latest.current.saleId, latest.current.scope);
    pendingRef.current = stored;
    setPending(stored);
    return stored;
  }, []);
  const accept = useCallback(
    async (result: PhysicalCashCheckoutResult, openCashDrawer = false): Promise<void> => {
      if (!active()) return;
      const owner = latest.current;
      const stored = loadPendingPhysicalCash(owner.saleId, owner.scope);
      if (!stored || result.operation_id !== stored.request.operation_id)
        throw new Error("Сохранённая оплата изменилась. Проверьте её состояние.");
      if (result.branch_id !== owner.branchId || result.register_id !== owner.registerId)
        throw new Error("Результат оплаты относится к другой кассе.");
      if (!accepted.current) {
        const displayed = await owner.onCompleted(result, openCashDrawer);
        if (!active()) return;
        if (!displayed)
          throw new Error("Оплата завершена. Не удалось открыть чек; проверьте оплату.");
        accepted.current = true;
      }
      if (!active()) return;
      const acknowledged = await acknowledgePhysicalCash(
        owner.saleId,
        result.operation_id,
        owner.scope,
      );
      if (!active()) return;
      if (!acknowledged)
        throw new Error("Оплата завершена. Не удалось сохранить подтверждение чека.");
      retain();
      setFinished(true);
      setError(null);
      owner.onBlockedChange(false);
    },
    [active, retain],
  );

  useEffect(() => {
    mounted.current = true;
    latest.current.onBlockedChange(initial.pending !== null || initial.error !== null);
    return () => {
      mounted.current = false;
    };
  }, [initial]);

  useEffect(() => {
    if (!initial.pending || !props.online || accepted.current || busyRef.current) return;
    let cancelled = false;
    let applying = false;
    const controller = new AbortController();
    const generation = ++busyGeneration.current;
    const ownsBusy = () => busyGeneration.current === generation;
    busyRef.current = true;
    setBusy(true);
    void getPhysicalCashOperation(initial.pending.request, latest.current.scope, controller.signal)
      .then(async (status) => {
        if (!cancelled && active() && status.state === "completed") {
          applying = true;
          await accept(status.result);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled && active()) setError(message(cause));
      })
      .finally(() => {
        if (ownsBusy()) {
          busyRef.current = false;
          if (active()) setBusy(false);
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
      if (!applying && ownsBusy()) busyRef.current = false;
    };
  }, [accept, active, initial.pending, props.online, props.scope.tenantId, props.scope.userId]);

  const itemsKey = JSON.stringify(props.items);
  useEffect(() => {
    setQuote(null);
    quoteItemsKey.current = null;
    if (
      pendingRef.current ||
      initial.error ||
      !props.canPay ||
      !props.online ||
      finished ||
      !props.items.length
    )
      return;
    let cancelled = false;
    const controller = new AbortController();
    setQuoteLoading(true);
    void getPhysicalCartQuote(props.saleId, controller.signal)
      .then(async (data) => {
        if (cancelled || !active()) return;
        const owner = latest.current;
        const checked = await parsePhysicalCashQuote(
          data,
          owner.saleId,
          owner.branchId,
          owner.registerId,
          owner.scope,
        );
        if (cancelled || !active()) return;
        if (
          checked.quote.lines.length !== owner.items.length ||
          checked.quote.lines.some((line) => {
            const item = owner.items.find((value) => value.id === line.id);
            return (
              !item ||
              item.tenant_id !== owner.scope.tenantId ||
              item.branch_id !== owner.branchId ||
              item.sale_id !== owner.saleId ||
              item.catalog_id !== line.catalog_id ||
              item.sale_format !== line.sale_format ||
              item.quantity !== line.quantity ||
              item.position !== line.position ||
              item.discount_amount !== line.discount_amount
            );
          })
        )
          throw new Error("Корзина изменилась. Обновите сумму перед оплатой.");
        setQuote(checked);
        quoteItemsKey.current = itemsKey;
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!cancelled && active()) setError(message(cause));
      })
      .finally(() => {
        if (!cancelled && active()) setQuoteLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    active,
    finished,
    initial.error,
    itemsKey,
    props.canPay,
    props.items.length,
    props.online,
    props.saleId,
    refresh,
  ]);

  async function submit(recovery: boolean): Promise<void> {
    if (busyRef.current || !active() || !latest.current.online) return;
    const owner = latest.current;
    let input;
    if (!recovery) {
      if (!owner.canPay || !quote || pendingRef.current || initial.error) return;
      if (quoteItemsKey.current !== JSON.stringify(owner.items)) {
        setError("Корзина изменилась. Обновите сумму перед оплатой.");
        return;
      }
      const values = formSchema.safeParse(form.getValues());
      if (!values.success) {
        form.setError("received", { message: "Введите сумму наличных, например 50 или 50,50." });
        return;
      }
      const tendered = normalizedMoney(values.data.received);
      if (cents(tendered) < cents(quote.quote.total_amount)) {
        form.setError("received", { message: "Полученная сумма меньше суммы чека." });
        return;
      }
      input = {
        schema_version: 2 as const,
        sale_id: owner.saleId,
        expected_revision_hash: quote.revision_hash,
        expected_total_amount: quote.quote.total_amount,
        cash_tendered: tendered,
        currency: "TJS" as const,
      };
    }
    busyRef.current = true;
    const generation = ++busyGeneration.current;
    owner.onBlockedChange(true);
    setBusy(true);
    setError(null);
    try {
      const result = await runPhysicalCash(owner.saleId, owner.scope, input);
      if (active()) await accept(result, !recovery);
    } catch (cause) {
      if (active()) {
        try {
          retain();
        } catch (storageError) {
          setError(message(storageError));
        }
        setError(message(cause));
      }
    } finally {
      if (busyGeneration.current === generation) {
        busyRef.current = false;
        if (active()) setBusy(false);
      }
    }
  }

  const total = pending?.request.expected_total_amount ?? quote?.quote.total_amount;
  const validReceived = receivedSchema.safeParse(received);
  const tendered =
    pending?.request.cash_tendered ??
    (validReceived.success ? normalizedMoney(validReceived.data) : null);
  const change =
    total && tendered && cents(tendered) >= cents(total)
      ? displayMoney(cents(tendered) - cents(total))
      : "—";
  return (
    <section
      className="space-y-4 rounded-lg border border-border bg-surface p-4"
      aria-label="Оплата наличными"
    >
      <h3 className="text-base font-semibold">Оплата наличными</h3>
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between">
          <dt>К оплате</dt>
          <dd className="font-semibold">{total ? `${total} TJS` : "—"}</dd>
        </div>
        <div className="flex justify-between">
          <dt>Сдача</dt>
          <dd>
            {change} {change !== "—" && "TJS"}
          </dd>
        </div>
      </dl>
      {finished ? (
        <p role="status">Продажа завершена. Чек сохранён.</p>
      ) : pending ? (
        <div className="space-y-3">
          <p className="text-sm text-foreground-muted">
            Оплата уже начата. Не принимайте деньги повторно. Проверьте эту же операцию.
          </p>
          <p className="text-sm">Получено: {pending.request.cash_tendered} TJS</p>
          <Button
            className="w-full"
            disabled={!props.online}
            isLoading={busy}
            onClick={() => void submit(true)}
          >
            Проверить и завершить оплату
          </Button>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(false);
          }}
        >
          <Label htmlFor={id}>Получено наличными, TJS</Label>
          <Input
            id={id}
            inputMode="decimal"
            autoComplete="off"
            disabled={busy || !props.canPay || !!initial.error || finished}
            invalid={!!form.formState.errors.received}
            {...form.register("received", { onChange: () => form.clearErrors("received") })}
          />
          {form.formState.errors.received && (
            <p role="alert" className="text-sm text-danger">
              {form.formState.errors.received.message}
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={!props.canPay || !props.online || !quote || quoteLoading || !!initial.error}
            isLoading={busy}
          >
            Завершить продажу
          </Button>
          {!quote && !initial.error && props.canPay && props.online && (
            <Button
              variant="ghost"
              className="w-full"
              disabled={quoteLoading || busy}
              onClick={() => setRefresh((value) => value + 1)}
            >
              {quoteLoading ? "Проверяем сумму…" : "Обновить сумму"}
            </Button>
          )}
          {!props.canPay && (
            <p className="text-sm text-foreground-muted">
              Оплата доступна для открытой смены и проверенной корзины.
            </p>
          )}
        </form>
      )}
      {!props.online && (
        <p role="status" className="text-sm text-foreground-muted">
          Нет связи с сервисом. Сохранённая оплата останется на этом устройстве.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
