import { useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  Badge,
  Button,
  Card,
  FormError,
  Input,
  Label,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { CashReceiptModal } from "@/features/billing/CashReceiptModal";
import {
  CASH_PAGE_SIZE,
  useCashPaymentCommands,
  usePlatformCashPayments,
} from "@/features/billing/cashQueries";
import { type CashPayment, type CashPaymentCreate } from "@/features/billing/cashTypes";
import { formatBillingDateTime, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";
import { createOperationId } from "@/lib/operationId";
import { type PlatformFinancialInvoice } from "./types";

const cashSchema = z.object({
  amount: z
    .string()
    .trim()
    .regex(/^\d{1,12}(?:[.,]\d{1,2})?$/, "Укажите сумму с точностью до 2 знаков")
    .refine((value) => Number(value.replace(",", ".")) > 0, "Сумма должна быть больше нуля"),
  paid_at: z
    .string()
    .min(1, "Укажите дату и время")
    .refine(
      (value) =>
        Number.isFinite(new Date(value).getTime()) && new Date(value).getTime() <= Date.now(),
      "Укажите прошедшую дату и время",
    ),
  purpose: z.string().trim().min(3, "Укажите назначение оплаты").max(500, "Не более 500 символов"),
  target_invoice_id: z.string(),
});
type CashForm = z.infer<typeof cashSchema>;

export function CashPaymentsWorkspace({
  tenantId,
  invoices,
  canReview,
  canApprove,
  online,
  refreshSignal,
}: {
  tenantId: string;
  invoices: readonly PlatformFinancialInvoice[];
  canReview: boolean;
  canApprove: boolean;
  online: boolean;
  refreshSignal: number;
}): JSX.Element {
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [decision, setDecision] = useState<{
    item: CashPayment;
    action: "approve" | "reject";
  } | null>(null);
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const query = usePlatformCashPayments(tenantId, page);
  const refetch = query.refetch;
  useEffect(() => {
    if (refreshSignal > 0) void refetch();
  }, [refetch, refreshSignal]);
  const completed = (message: string) => {
    setCreateOpen(false);
    setDecision(null);
    setNotice(message);
  };
  return (
    <Card className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold">Наличные оплаты</h3>
          <p className="mt-1 text-sm text-foreground-muted">
            Приём денег и независимое подтверждение другим сотрудником Aurum.
          </p>
        </div>
        {canReview ? (
          <Button
            size="sm"
            disabled={!online}
            onClick={() => {
              setNotice(null);
              setCreateOpen(true);
            }}
          >
            Зарегистрировать наличные
          </Button>
        ) : null}
      </div>
      {notice ? (
        <p
          role="status"
          className="rounded-lg bg-success-subtle p-3 text-sm text-success-foreground"
        >
          {notice}
        </p>
      ) : null}
      {!online ? (
        <p role="status" className="text-sm text-warning-foreground">
          Нет подключения или финансовый журнал требует проверки. Денежные действия временно
          недоступны.
        </p>
      ) : null}
      {query.isLoading ? (
        <SkeletonRows rows={3} />
      ) : query.error ? (
        <div className="space-y-2" role="alert">
          <p className="text-sm text-danger-foreground">
            {describeApiError(query.error, "Не удалось загрузить наличные оплаты")}
          </p>
          <Button
            size="sm"
            variant="secondary"
            isLoading={query.isFetching}
            onClick={() => void refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : !query.data?.items.length ? (
        <TableEmpty title="Наличных оплат пока нет">
          Зарегистрируйте полученную сумму. Подтверждение и документ доступны после проверки другим
          сотрудником.
        </TableEmpty>
      ) : (
        <>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
            {query.data.items.map((item) => (
              <li key={item.id} className="space-y-3 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-semibold">
                      {formatBillingMoney(item.amount, item.currency)}
                    </p>
                    <p className="mt-1 break-words text-sm">{item.purpose}</p>
                    <p className="mt-1 text-xs text-foreground-muted">
                      {item.document_number} · {formatBillingDateTime(item.paid_at)}
                    </p>
                  </div>
                  <Badge
                    tone={
                      item.status === "confirmed"
                        ? "success"
                        : item.status === "rejected"
                          ? "neutral"
                          : "warning"
                    }
                  >
                    {item.status === "confirmed"
                      ? "Подтверждено"
                      : item.status === "rejected"
                        ? "Отклонено"
                        : "На подтверждении"}
                  </Badge>
                </div>
                {item.status === "pending_approval" ? (
                  <p className="text-xs text-foreground-muted">
                    Сумма ещё не включена в баланс.
                    {canApprove && !item.can_approve
                      ? " Эту оплату должен подтвердить другой сотрудник."
                      : ""}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {item.status === "confirmed" ? (
                    <Button size="sm" variant="secondary" onClick={() => setReceiptId(item.id)}>
                      Открыть документ
                    </Button>
                  ) : null}
                  {canApprove && item.status === "pending_approval" ? (
                    <>
                      <Button
                        size="sm"
                        disabled={!online || !item.can_approve}
                        onClick={() => setDecision({ item, action: "approve" })}
                      >
                        Подтвердить наличные
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={!online || !item.can_approve}
                        onClick={() => setDecision({ item, action: "reject" })}
                      >
                        Отклонить
                      </Button>
                    </>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
          {query.data.total > CASH_PAGE_SIZE ? (
            <Pagination
              page={page}
              pageSize={CASH_PAGE_SIZE}
              total={query.data.total}
              onPage={setPage}
            />
          ) : null}
        </>
      )}
      {createOpen ? (
        <RegisterCashModal
          tenantId={tenantId}
          invoices={invoices}
          online={online}
          onClose={() => setCreateOpen(false)}
          onCompleted={completed}
        />
      ) : null}
      {decision ? (
        <CashDecisionModal
          tenantId={tenantId}
          item={decision.item}
          action={decision.action}
          online={online}
          onClose={() => setDecision(null)}
          onCompleted={completed}
        />
      ) : null}
      {receiptId ? (
        <CashReceiptModal id={receiptId} tenantId={tenantId} onClose={() => setReceiptId(null)} />
      ) : null}
    </Card>
  );
}

function RegisterCashModal({
  tenantId,
  invoices,
  online,
  onClose,
  onCompleted,
}: {
  tenantId: string;
  invoices: readonly PlatformFinancialInvoice[];
  online: boolean;
  onClose: () => void;
  onCompleted: (message: string) => void;
}): JSX.Element {
  const { create } = useCashPaymentCommands(tenantId);
  const operationId = useRef(createOperationId());
  const attemptedPayload = useRef<CashPaymentCreate | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const form = useForm<CashForm>({
    defaultValues: {
      amount: "",
      paid_at: localDateTime(),
      purpose: "Оплата услуг Aurum Pharma",
      target_invoice_id: "",
    },
  });
  const submit = form.handleSubmit(async (values) => {
    if (!online || create.isPending) return;
    const parsed = cashSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (
          field === "amount" ||
          field === "paid_at" ||
          field === "purpose" ||
          field === "target_invoice_id"
        )
          form.setError(field, { message: issue.message });
      }
      return;
    }
    const payload = attemptedPayload.current ?? {
      operation_id: operationId.current,
      amount: parsed.data.amount.replace(",", "."),
      paid_at: new Date(parsed.data.paid_at).toISOString(),
      purpose: parsed.data.purpose,
      target_invoice_id: parsed.data.target_invoice_id || null,
    };
    attemptedPayload.current = payload;
    setLocked(true);
    setError(null);
    try {
      await create.mutateAsync(payload);
      onCompleted(
        "Регистрация сохранена. Оплата учитывается после подтверждения другим сотрудником Aurum.",
      );
    } catch (failure) {
      if (
        isAxiosError(failure) &&
        (failure.response?.status === 400 || failure.response?.status === 422)
      ) {
        attemptedPayload.current = null;
        setLocked(false);
      }
      setError(
        describeApiError(
          failure,
          "Результат регистрации не подтверждён. Повторите отправку с теми же данными или проверьте список оплат.",
        ),
      );
    }
  });
  return (
    <Modal
      open
      onClose={() => {
        if (!create.isPending) onClose();
      }}
      title="Регистрация наличной оплаты"
    >
      <form className="space-y-4" noValidate onSubmit={submit}>
        <p className="text-sm text-foreground-muted">
          Регистрируйте только фактически полученные деньги. Сумма попадёт в баланс после
          независимого подтверждения.
        </p>
        <div>
          <Label htmlFor="cash-amount">Сумма, TJS</Label>
          <Input
            id="cash-amount"
            autoFocus
            inputMode="decimal"
            disabled={locked || create.isPending}
            {...form.register("amount")}
          />
          <FormError>{form.formState.errors.amount?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="cash-paid-at">Дата и время получения</Label>
          <Input
            id="cash-paid-at"
            type="datetime-local"
            disabled={locked || create.isPending}
            {...form.register("paid_at")}
          />
          <FormError>{form.formState.errors.paid_at?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="cash-purpose">Назначение оплаты</Label>
          <Input
            id="cash-purpose"
            maxLength={500}
            disabled={locked || create.isPending}
            {...form.register("purpose")}
          />
          <FormError>{form.formState.errors.purpose?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="cash-invoice">Зачисление</Label>
          <Select
            id="cash-invoice"
            disabled={locked || create.isPending}
            {...form.register("target_invoice_id")}
          >
            <option value="">Аванс без привязки к счёту</option>
            {invoices
              .filter(
                (item) => item.document_state === "issued" && Number(item.outstanding_amount) > 0,
              )
              .map((item) => (
                <option key={item.invoice_id} value={item.invoice_id}>
                  Счёт {item.invoice_number} ·{" "}
                  {formatBillingMoney(item.outstanding_amount, item.currency)}
                </option>
              ))}
          </Select>
          <p className="mt-1 text-xs text-foreground-muted">
            Превышение остатка выбранного счёта сохраняется как аванс.
          </p>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-danger-foreground">
            {error}
          </p>
        ) : null}
        {locked && error ? (
          <p className="text-xs text-foreground-muted">
            Данные сохранены для безопасного повтора. Перед новой регистрацией проверьте, не
            появилась ли эта оплата в списке.
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button variant="secondary" disabled={create.isPending} onClick={onClose}>
            Закрыть
          </Button>
          <Button type="submit" disabled={!online} isLoading={create.isPending}>
            {locked ? "Повторить отправку" : "Передать на подтверждение"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function CashDecisionModal({
  tenantId,
  item,
  action,
  online,
  onClose,
  onCompleted,
}: {
  tenantId: string;
  item: CashPayment;
  action: "approve" | "reject";
  online: boolean;
  onClose: () => void;
  onCompleted: (message: string) => void;
}): JSX.Element {
  const commands = useCashPaymentCommands(tenantId);
  const operationId = useRef(createOperationId());
  const [error, setError] = useState<string | null>(null);
  const form = useForm<{ checked: boolean; reason: string }>({
    defaultValues: { checked: false, reason: "" },
  });
  const pending = commands.approve.isPending || commands.reject.isPending;
  const submit = form.handleSubmit(async (values) => {
    if (!online || !item.can_approve || pending) return;
    const checked = z
      .boolean()
      .refine((value) => value, "Подтвердите проверку наличных")
      .safeParse(values.checked);
    if (action === "approve" && !checked.success) {
      form.setError("checked", { message: checked.error.issues[0]?.message });
      return;
    }
    const reason = z
      .string()
      .trim()
      .min(5, "Укажите причину: минимум 5 символов")
      .max(500, "Не более 500 символов")
      .safeParse(values.reason);
    if (action === "reject" && !reason.success) {
      form.setError("reason", { message: reason.error.issues[0]?.message });
      return;
    }
    setError(null);
    const payload = { operation_id: operationId.current, expected_row_version: item.row_version };
    try {
      if (action === "approve") {
        await commands.approve.mutateAsync({ id: item.id, payload });
        onCompleted("Наличные подтверждены и отражены в балансе. Документ доступен клиенту.");
      } else {
        await commands.reject.mutateAsync({
          id: item.id,
          payload: { ...payload, reason: reason.success ? reason.data : "" },
        });
        onCompleted("Регистрация отклонена. Сумма не включена в баланс.");
      }
    } catch (failure) {
      setError(
        describeApiError(
          failure,
          "Не удалось подтвердить результат. Проверьте состояние оплаты перед повтором.",
        ),
      );
    }
  });
  return (
    <Modal
      open
      onClose={() => {
        if (!pending) onClose();
      }}
      title={action === "approve" ? "Подтвердить наличные" : "Отклонить регистрацию"}
    >
      <form className="space-y-4" noValidate onSubmit={submit}>
        <p className="font-semibold">
          {formatBillingMoney(item.amount, item.currency)} · {item.document_number}
        </p>
        <p className="break-words text-sm">{item.purpose}</p>
        <p className="text-sm text-foreground-muted">
          Получено: {formatBillingDateTime(item.paid_at)}
        </p>
        {action === "approve" ? (
          <div>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                disabled={pending}
                {...form.register("checked")}
              />
              <span>Я независимо проверил получение наличных, сумму и назначение оплаты.</span>
            </label>
            <FormError>{form.formState.errors.checked?.message}</FormError>
          </div>
        ) : (
          <div>
            <Label htmlFor="cash-reject-reason">Причина отклонения</Label>
            <Input
              id="cash-reject-reason"
              autoFocus
              maxLength={500}
              disabled={pending}
              {...form.register("reason")}
            />
            <FormError>{form.formState.errors.reason?.message}</FormError>
          </div>
        )}
        {error ? (
          <p role="alert" className="text-sm text-danger-foreground">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            Закрыть
          </Button>
          <Button type="submit" disabled={!online || !item.can_approve} isLoading={pending}>
            {action === "approve" ? "Подтвердить получение" : "Отклонить регистрацию"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function localDateTime(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
