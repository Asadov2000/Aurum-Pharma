import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Input, Label } from "@/components/ui";
import { cashCheckoutContracts } from "./physicalCashCheckout";
import { type SaleDetails } from "./types";
import { type PosStorageScope } from "./posStorageScope";
import { hasTrainingCheckout } from "./trainingCheckoutPresence";
import {
  matchesTrainingCompletedSale,
  acknowledgeTrainingCheckout,
  getTrainingQuote,
  inspectTrainingCheckout,
  startTrainingCheckout,
  retryTrainingCheckout,
  type TrainingCheckoutResult,
  type TrainingContext,
  type TrainingQuote,
  trainingCheckoutRequestSchema,
} from "./trainingCheckout";

const fieldsSchema = z
  .object({
    cash: cashCheckoutContracts.money,
    card: cashCheckoutContracts.money,
    qr: cashCheckoutContracts.money,
    tendered: cashCheckoutContracts.money,
  })
  .strict();
type Fields = z.infer<typeof fieldsSchema>;
interface Props {
  sale: SaleDetails;
  scope: PosStorageScope;
  registerId: string;
  canStart: boolean;
  paymentMethods: readonly string[];
  mixedPaymentEnabled: boolean;
  sessionIsActive: () => boolean;
  onBlockedChange: (blocked: boolean) => void;
  refreshSale: () => Promise<SaleDetails | undefined>;
  onCompleted: (result: TrainingCheckoutResult) => boolean | Promise<boolean>;
  onPrint: () => void;
  onNewSale: () => void;
}
export function TrainingCheckoutPanel(props: Props): JSX.Element {
  const live = useRef(props);
  live.current = props;
  const [busy, setBusy] = useState(false),
    [pending, setPending] = useState(() => hasTrainingCheckout(props.sale.id, props.scope));
  const [quote, setQuote] = useState<{ quote: TrainingQuote; revision_hash: string } | null>(null);
  const [result, setResult] = useState<TrainingCheckoutResult | null>(null),
    [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null),
    [accepted, setAccepted] = useState(false);
  const running = useRef(false),
    mounted = useRef(true);
  const owner = useRef({
    saleId: props.sale.id,
    tenantId: props.scope.tenantId,
    userId: props.scope.userId,
    registerId: props.registerId,
    branchId: props.sale.branch_id,
  });
  const current = useCallback(() => {
    const p = live.current,
      s = owner.current;
    return (
      mounted.current &&
      p.sessionIsActive() &&
      p.sale.id === s.saleId &&
      p.scope.tenantId === s.tenantId &&
      p.scope.userId === s.userId &&
      p.registerId === s.registerId &&
      p.sale.branch_id === s.branchId
    );
  }, []);
  const context = useCallback(
    (): TrainingContext => ({
      scope: owner.current,
      active: current,
      isTest: () => live.current.sale.is_test === true,
      writable: () => live.current.canStart,
    }),
    [current],
  );
  const form = useForm<Fields>({
    defaultValues: { cash: "0.00", card: "0.00", qr: "0.00", tendered: "0.00" },
  });
  const accept = useCallback(
    async (value: TrainingCheckoutResult) => {
      if (!current()) return;
      setResult(value);
      const saved = await live.current.refreshSale();
      if (!current()) return;
      if (!saved || !matchesTrainingCompletedSale(saved, value))
        throw new Error("Учебный чек не подтверждён сервером.");
      if (await live.current.onCompleted(value)) {
        if (current()) setAccepted(true);
      }
    },
    [current],
  );
  const run = useCallback(
    async (action: () => Promise<void>) => {
      if (running.current || !current()) return;
      running.current = true;
      setBusy(true);
      setError(null);
      try {
        await action();
      } catch {
        if (current())
          setError(
            "Не удалось подтвердить учебную операцию. Проверьте сохранённый результат; реальную оплату не выполняйте.",
          );
      } finally {
        if (current()) {
          setPending(hasTrainingCheckout(owner.current.saleId, owner.current));
          running.current = false;
          setBusy(false);
        }
      }
    },
    [current],
  );
  const inspect = useCallback(
    () =>
      run(async () => {
        const recovered = await inspectTrainingCheckout(context());
        if (!current()) return;
        setNotFound(recovered?.state === "not_found");
        if (recovered?.state === "completed") await accept(recovered.result);
      }),
    [run, context, current, accept],
  );
  useEffect(() => {
    mounted.current = true;
    void inspect();
    return () => {
      mounted.current = false;
    };
  }, [inspect]);
  const { onBlockedChange } = props;
  useEffect(() => {
    onBlockedChange(pending || busy);
  }, [pending, busy, onBlockedChange]);
  const calculate = () =>
    run(async () => {
      const value = await getTrainingQuote(context());
      if (!current()) return;
      setQuote(value);
      form.reset({
        cash: value.quote.total_amount,
        card: "0.00",
        qr: "0.00",
        tendered: value.quote.total_amount,
      });
    });
  const submit = form.handleSubmit((values) =>
    run(async () => {
      if (!quote || !live.current.canStart) return;
      const checked = fieldsSchema.parse(values),
        payments = (["cash", "card", "qr"] as const)
          .filter((method) => cashCheckoutContracts.cents(checked[method]) > 0n)
          .map((method) => ({ method, amount: checked[method] }));
      if (
        payments.some((p) => !live.current.paymentMethods.includes(p.method)) ||
        (!live.current.mixedPaymentEnabled && payments.length !== 1)
      )
        throw new Error("method");
      const request = trainingCheckoutRequestSchema.parse({
        schema_version: 1,
        operation_id: "00000000-0000-4000-8000-000000000000",
        sale_id: owner.current.saleId,
        expected_revision_hash: quote.revision_hash,
        expected_total_amount: quote.quote.total_amount,
        simulated_payments: payments,
        cash_tendered: checked.tendered,
        currency: "TJS",
      });
      const { operation_id: unused, ...input } = request;
      void unused;
      const value = await startTrainingCheckout(context(), input);
      if (current()) await accept(value);
    }),
  );
  return (
    <section
      role="region"
      aria-label="Учебная продажа"
      className="space-y-3 rounded-lg border border-warning p-3"
    >
      <h2 className="font-semibold">Учебная продажа</h2>
      <p>
        TEST: деньги и товар не списываются. Карта и QR только имитируются; терминал не используйте.
      </p>
      {error && <p role="alert">{error}</p>}
      {result ? (
        <>
          <p role="status">
            Учебный чек {result.receipt_number} · {result.total_amount} TJS
          </p>
          <p>Учебная сдача: {result.receipt.change_amount} TJS</p>
          <Button type="button" disabled={busy || !accepted} onClick={props.onPrint}>
            Открыть TEST-чек
          </Button>
          {!accepted && (
            <Button type="button" disabled={busy} onClick={() => void inspect()}>
              Проверить учебную операцию
            </Button>
          )}
          <Button
            type="button"
            disabled={busy || !accepted}
            onClick={() =>
              void run(async () => {
                await acknowledgeTrainingCheckout(context());
                if (!current()) return;
                live.current.onBlockedChange(false);
                live.current.onNewSale();
              })
            }
          >
            Новая продажа
          </Button>
        </>
      ) : pending ? (
        <>
          <p role="status">Есть сохранённая учебная операция. Новая оплата не требуется.</p>
          <Button type="button" disabled={busy} onClick={() => void inspect()}>
            Проверить учебную операцию
          </Button>
          {notFound && (
            <Button
              type="button"
              disabled={busy || !props.canStart}
              onClick={() =>
                void run(async () => {
                  const value = await retryTrainingCheckout(context());
                  if (current()) await accept(value);
                })
              }
            >
              Повторить учебную команду
            </Button>
          )}
        </>
      ) : (
        <>
          <Button type="button" disabled={busy || !props.canStart} onClick={() => void calculate()}>
            Рассчитать учебный чек
          </Button>
          {quote && (
            <form onSubmit={submit} className="space-y-2">
              <p>Учебная сумма: {quote.quote.total_amount} TJS</p>
              {(
                [
                  ["cash", "Учебные наличные, TJS"],
                  ["card", "Учебная карта, TJS"],
                  ["qr", "Учебный QR, TJS"],
                  ["tendered", "Учебно получено наличными, TJS"],
                ] as const
              ).map(([name, label]) => (
                <div key={name}>
                  <Label htmlFor={`training-${name}`}>{label}</Label>
                  <Input
                    id={`training-${name}`}
                    inputMode="decimal"
                    {...form.register(name)}
                    disabled={busy || !props.canStart}
                  />
                </div>
              ))}
              <Button type="submit" disabled={busy || !props.canStart}>
                Завершить учебную продажу
              </Button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
