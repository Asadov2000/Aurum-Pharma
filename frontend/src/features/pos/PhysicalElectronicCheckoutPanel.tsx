import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Button, Input, Label } from "@/components/ui";
import {
  evidenceSchema,
  executeElectronicCommand,
  loadElectronicCommand,
  hasRejectedElectronicCommand,
  releaseRejectedElectronicCommand,
  recoverElectronicCheckout,
  type ElectronicAttempt,
  type ElectronicCheckoutContext,
  type ElectronicCheckoutResult,
  type ElectronicCommand,
} from "./unitElectronicCheckout";

interface Props {
  context: ElectronicCheckoutContext;
  writable: boolean;
  canAccept?: boolean;
  canResolveDecline?: boolean;
  online: boolean;
  onBarrierChange: (blocked: boolean) => void;
  onCompleted: (result: ElectronicCheckoutResult) => Promise<boolean>;
}
/** Operator evidence is explicit; this feature never calls a bank or terminal. */
export function PhysicalElectronicCheckoutPanel(props: Props): JSX.Element {
  const latest = useRef(props);
  latest.current = props;
  const active = useRef(false),
    running = useRef(false),
    epoch = useRef(0);
  const [busy, setBusy] = useState(true),
    [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<ElectronicAttempt[]>([]);
  const [result, setResult] = useState<ElectronicCheckoutResult | null>(null);
  const [rejected, setRejected] = useState(false);
  const [pending, setPending] = useState<ElectronicCommand | null>(null);
  const form = useForm<{ terminal_id: string; external_reference: string }>({
    defaultValues: { terminal_id: "", external_reference: "" },
  });
  const run = useCallback(
    async (command?: ElectronicCommand, releaseRejected = false): Promise<void> => {
      if (!active.current || running.current) return;
      const version = epoch.current,
        owner = latest.current;
      const sameOwner = () =>
        active.current &&
        epoch.current === version &&
        owner.context.isActive() &&
        latest.current.context.scope.tenantId === owner.context.scope.tenantId &&
        latest.current.context.scope.userId === owner.context.scope.userId &&
        latest.current.context.registerId === owner.context.registerId &&
        latest.current.context.branchId === owner.context.branchId &&
        latest.current.context.intent.operation_id === owner.context.intent.operation_id;
      const check = () => {
        if (
          !sameOwner() ||
          ((command !== undefined || releaseRejected) &&
            (!latest.current.writable || !latest.current.online))
        )
          throw new Error("Касса, разрешение или сессия изменились. Данные сохранены.");
      };
      const current = () => {
        try {
          check();
          return true;
        } catch {
          return false;
        }
      };
      const context = { ...owner.context, isActive: current };
      running.current = true;
      setBusy(true);
      setError(null);
      owner.onBarrierChange(true);
      try {
        check();
        if (!owner.online) throw new Error("Нужно соединение. Не принимайте деньги повторно.");
        if (releaseRejected) {
          if (!owner.writable) throw new Error("Исправление реквизитов недоступно.");
          await releaseRejectedElectronicCommand(context);
          check();
        }
        if (command) {
          if (!owner.writable) throw new Error("Финансовые действия недоступны.");
          await executeElectronicCommand(context, command);
          check();
        }
        const state = await recoverElectronicCheckout(context);
        check();
        const stored = loadElectronicCommand(context);
        setPending(stored);
        setRejected(hasRejectedElectronicCommand(context));
        setAttempts(state.attempts);
        setResult(state.result);
        owner.onBarrierChange(
          stored !== null ||
            state.result !== null ||
            state.attempts.some((a) => a.status !== "voided"),
        );
      } catch (cause) {
        if (sameOwner()) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Результат неизвестен. Проверьте эту же операцию.",
          );
          try {
            setPending(loadElectronicCommand(context));
            setRejected(hasRejectedElectronicCommand(context));
          } catch {
            /* Corrupt storage keeps the barrier. */
          }
        }
      } finally {
        if (sameOwner()) {
          running.current = false;
          setBusy(false);
        }
      }
    },
    [],
  );
  useEffect(() => {
    active.current = true;
    epoch.current += 1;
    running.current = false;
    setAttempts([]);
    setResult(null);
    setPending(null);
    setRejected(false);
    void run();
    return () => {
      active.current = false;
      epoch.current += 1;
    };
    // Scope changes remount the recovery owner; loading never submits a command.
  }, [
    props.context.intent.operation_id,
    props.context.scope.tenantId,
    props.context.scope.userId,
    props.context.branchId,
    props.context.registerId,
    props.online,
    run,
  ]);
  const disabled = busy || !props.online || !props.writable || pending !== null || error !== null;
  const confirm = (row: ElectronicAttempt) =>
    form.handleSubmit((fields) => {
      const parsed = evidenceSchema.safeParse(fields);
      if (!parsed.success) {
        setError("Введите терминал и внешний номер подтверждения без управляющих символов.");
        return;
      }
      void run({ action: "confirm", attemptId: row.id, payload: parsed.data });
    });
  async function accept(): Promise<void> {
    if (!result || busy || running.current || props.canAccept === false) return;
    const owner = latest.current,
      version = epoch.current;
    const same = () =>
      active.current &&
      epoch.current === version &&
      owner.context.isActive() &&
      latest.current.context.intent.operation_id === result.operation_id &&
      latest.current.context.registerId === result.register_id &&
      latest.current.context.branchId === result.branch_id;
    running.current = true;
    setBusy(true);
    try {
      if (!same() || !(await owner.onCompleted(result)))
        throw new Error("Проверьте сохранённый чек повторно.");
    } catch (cause) {
      if (same()) setError(cause instanceof Error ? cause.message : "Чек ещё не принят.");
    } finally {
      if (same()) {
        running.current = false;
        setBusy(false);
      }
    }
  }

  return (
    <section aria-label="Электронное проведение" className="space-y-2 rounded border p-3">
      <p>
        Реквизиты вводит оператор после проверки оплаты. Это не банковская гарантия; вызовов
        терминала нет.
      </p>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">Проверяем сохранённую оплату…</p>}
      {result ? (
        <div>
          <p role="status">
            Сохранённый чек {result.receipt_number}: {result.total_amount} TJS
          </p>
          <Button
            type="button"
            disabled={busy || !props.online || props.canAccept === false}
            onClick={() => void accept()}
          >
            Принять сохранённый чек
          </Button>
        </div>
      ) : (
        <>
          {attempts.length === 0 && (
            <Button
              type="button"
              disabled={disabled}
              onClick={() => {
                const ids: { card?: string; qr?: string } = {};
                for (const part of props.context.intent.plan.request.payments)
                  if (part.method !== "cash") ids[part.method] = crypto.randomUUID();
                void run({ action: "attempts", payload: { attempt_ids: ids } });
              }}
            >
              Подготовить полный набор попыток
            </Button>
          )}
          <form onSubmit={(event) => event.preventDefault()}>
            <Label htmlFor="electronic-terminal">Терминал</Label>
            <Input id="electronic-terminal" {...form.register("terminal_id")} disabled={disabled} />
            <Label htmlFor="electronic-reference">Внешний номер подтверждения</Label>
            <Input
              id="electronic-reference"
              {...form.register("external_reference")}
              disabled={disabled}
            />
            {attempts.map((row) => (
              <div key={row.id}>
                <p>
                  {row.payment_method}: {row.amount} TJS — {row.status}
                </p>
                {row.status === "pending" && (
                  <>
                    <Button
                      type="button"
                      disabled={disabled}
                      onClick={() =>
                        void run({ action: "reconciliation", attemptId: row.id, payload: {} })
                      }
                    >
                      Начать явную сверку {row.payment_method}
                    </Button>
                    <Button
                      type="button"
                      disabled={disabled}
                      onClick={() =>
                        void run({
                          action: "void",
                          attemptId: row.id,
                          payload: { reason: "cashier_cancelled" },
                        })
                      }
                    >
                      Отменить неподтверждённую {row.payment_method}
                    </Button>
                  </>
                )}
                {row.status === "requires_reconciliation" && (
                  <>
                    <Button type="button" disabled={disabled} onClick={() => void confirm(row)()}>
                      Подтвердить проверенную оплату {row.payment_method}
                    </Button>
                    <Button
                      type="button"
                      disabled={disabled || !props.canResolveDecline}
                      onClick={form.handleSubmit((fields) => {
                        const parsed = evidenceSchema.safeParse(fields);
                        if (!parsed.success) {
                          setError("Для отмены после сверки нужны реквизиты.");
                          return;
                        }
                        void run({
                          action: "void",
                          attemptId: row.id,
                          payload: { reason: "terminal_declined", ...parsed.data },
                        });
                      })}
                    >
                      Подтвердить отказ {row.payment_method} (права управляющего)
                    </Button>
                  </>
                )}
              </div>
            ))}
          </form>
          <Button
            type="button"
            disabled={
              disabled || attempts.length === 0 || attempts.some((a) => a.status !== "confirmed")
            }
            onClick={() => void run({ action: "complete", payload: {} })}
          >
            Завершить продажу по сохранённому плану
          </Button>
        </>
      )}
      {rejected && !result && (
        <Button
          type="button"
          disabled={busy || !props.online || !props.writable}
          onClick={() => void run(undefined, true)}
        >
          Исправить отклонённые реквизиты
        </Button>
      )}
      {pending && !result && !rejected && (
        <Button
          type="button"
          disabled={busy || !props.online || !props.writable}
          onClick={() => void run(pending)}
        >
          Повторить исходную команду без изменения данных
        </Button>
      )}
      <Button type="button" disabled={busy || !props.online} onClick={() => void run()}>
        Проверить сохранённый результат оплаты
      </Button>
    </section>
  );
}
