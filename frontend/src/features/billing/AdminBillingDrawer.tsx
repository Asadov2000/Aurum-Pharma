import { useState } from "react";
import { useForm } from "react-hook-form";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormError,
  Input,
  Label,
  Modal,
  Select,
  Textarea,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { billingPeriodLabel, paymentMethodLabel, paymentMethodOptions } from "./labels";
import {
  useCreateInvoice,
  useCreateSubscription,
  usePlansQuery,
  useRecordPayment,
} from "./queries";
import { type BillingPeriod, type PaymentMethod } from "./types";

// Admin can WRITE for any tenant but cannot READ tenant-scoped data
// (no admin GET endpoints in phase 1). So we surface the resulting IDs
// so the operator can copy them into the next form / hand them to the
// tenant user. Created entities are visible from the owner's /billing.

export function AdminBillingDrawer({
  tenantId,
  tenantName,
  onClose,
}: {
  tenantId: string;
  tenantName: string;
  onClose: () => void;
}): JSX.Element {
  const [lastSubId, setLastSubId] = useState<string | null>(null);
  const [lastInvoiceId, setLastInvoiceId] = useState<string | null>(null);

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={`Биллинг: ${tenantName}`}
      className="max-w-3xl"
    >
      <div className="space-y-4">
        <p className="rounded-md bg-foreground/[0.03] px-3 py-2 text-xs text-foreground-secondary">
          Поля только для записи. Прочитать созданное через эту панель нельзя —
          в Этапе 1 нет admin-эндпоинтов чтения. Тенант увидит изменения на
          своей странице «Биллинг». ID результатов появляются ниже каждой формы.
        </p>

        <SubscriptionForm
          tenantId={tenantId}
          onCreated={(id) => setLastSubId(id)}
          lastId={lastSubId}
        />

        <InvoiceForm
          tenantId={tenantId}
          defaultSubscriptionId={lastSubId ?? ""}
          onCreated={(id) => setLastInvoiceId(id)}
          lastId={lastInvoiceId}
        />

        <PaymentForm
          tenantId={tenantId}
          defaultInvoiceId={lastInvoiceId ?? ""}
        />

        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// -----------------------------------------------------------------------------

interface SubForm {
  plan_id: string;
  billing_period: BillingPeriod;
  branches_count: number;
}

function SubscriptionForm({
  tenantId,
  onCreated,
  lastId,
}: {
  tenantId: string;
  onCreated: (id: string) => void;
  lastId: string | null;
}): JSX.Element {
  const plans = usePlansQuery();
  const create = useCreateSubscription();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<SubForm>({
    defaultValues: { plan_id: "", billing_period: "monthly", branches_count: 1 },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (!values.plan_id) {
      form.setError("plan_id", { message: "Выберите план" });
      return;
    }
    setTopError(null);
    try {
      const sub = await create.mutateAsync({
        tenantId,
        payload: {
          plan_id: values.plan_id,
          billing_period: values.billing_period,
          branches_count: Number(values.branches_count),
        },
      });
      onCreated(sub.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось создать подписку"));
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>1. Подписка</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="grid grid-cols-3 gap-3">
          <div>
            <Label htmlFor="plan_id">План</Label>
            <Select
              id="plan_id"
              invalid={Boolean(form.formState.errors.plan_id)}
              {...form.register("plan_id")}
            >
              <option value="">— выберите —</option>
              {plans.data
                ?.filter((p) => p.is_active)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.code})
                  </option>
                ))}
            </Select>
            <FormError>{form.formState.errors.plan_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="billing_period">Период</Label>
            <Select id="billing_period" {...form.register("billing_period")}>
              <option value="monthly">{billingPeriodLabel.monthly}</option>
              <option value="yearly">{billingPeriodLabel.yearly}</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="branches_count">Точек</Label>
            <Input
              id="branches_count"
              type="number"
              min={1}
              {...form.register("branches_count", { valueAsNumber: true })}
            />
          </div>
          {topError && (
            <p className="col-span-3 text-sm text-danger">{topError}</p>
          )}
          {lastId && (
            <p className="col-span-3 text-xs text-success-foreground">
              ✅ Подписка создана:{" "}
              <code className="rounded bg-success-subtle px-1.5 py-0.5 font-mono">
                {lastId}
              </code>
            </p>
          )}
          <div className="col-span-3 flex justify-end">
            <Button type="submit" size="sm" isLoading={create.isPending}>
              Создать подписку
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// -----------------------------------------------------------------------------

interface InvForm {
  subscription_id: string;
  amount: string;
  due_in_days: number;
  discount_amount: string;
  discount_reason: string;
  notes: string;
}

function InvoiceForm({
  tenantId,
  defaultSubscriptionId,
  onCreated,
  lastId,
}: {
  tenantId: string;
  defaultSubscriptionId: string;
  onCreated: (id: string) => void;
  lastId: string | null;
}): JSX.Element {
  const create = useCreateInvoice();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<InvForm>({
    defaultValues: {
      subscription_id: defaultSubscriptionId,
      amount: "",
      due_in_days: 7,
      discount_amount: "",
      discount_reason: "",
      notes: "",
    },
    values: defaultSubscriptionId
      ? {
          subscription_id: defaultSubscriptionId,
          amount: "",
          due_in_days: 7,
          discount_amount: "",
          discount_reason: "",
          notes: "",
        }
      : undefined,
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (!values.subscription_id) {
      form.setError("subscription_id", { message: "Введите ID подписки" });
      return;
    }
    if (Number(values.amount) <= 0) {
      form.setError("amount", { message: "Сумма больше 0" });
      return;
    }
    setTopError(null);
    try {
      const inv = await create.mutateAsync({
        tenantId,
        payload: {
          subscription_id: values.subscription_id,
          amount: values.amount,
          due_in_days: Number(values.due_in_days),
          discount_amount: values.discount_amount ? values.discount_amount : "0",
          discount_reason: values.discount_reason.trim() || null,
          notes: values.notes.trim() || null,
        },
      });
      onCreated(inv.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось создать счёт"));
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>2. Счёт</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="grid grid-cols-3 gap-3">
          <div className="col-span-3">
            <Label htmlFor="subscription_id">ID подписки</Label>
            <Input
              id="subscription_id"
              placeholder="UUID подписки"
              invalid={Boolean(form.formState.errors.subscription_id)}
              {...form.register("subscription_id")}
            />
            <FormError>{form.formState.errors.subscription_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="amount">Сумма</Label>
            <Input
              id="amount"
              type="text"
              inputMode="decimal"
              invalid={Boolean(form.formState.errors.amount)}
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="due_in_days">Срок (дней)</Label>
            <Input
              id="due_in_days"
              type="number"
              min={0}
              max={365}
              {...form.register("due_in_days", { valueAsNumber: true })}
            />
          </div>
          <div>
            <Label htmlFor="discount_amount">Скидка</Label>
            <Input
              id="discount_amount"
              type="text"
              inputMode="decimal"
              {...form.register("discount_amount")}
            />
          </div>
          <div className="col-span-2">
            <Label htmlFor="discount_reason">Причина скидки</Label>
            <Input id="discount_reason" {...form.register("discount_reason")} />
          </div>
          <div>
            <Label htmlFor="notes">Заметки</Label>
            <Textarea id="notes" rows={1} {...form.register("notes")} />
          </div>
          {topError && (
            <p className="col-span-3 text-sm text-danger">{topError}</p>
          )}
          {lastId && (
            <p className="col-span-3 text-xs text-success-foreground">
              ✅ Счёт создан:{" "}
              <code className="rounded bg-success-subtle px-1.5 py-0.5 font-mono">
                {lastId}
              </code>
            </p>
          )}
          <div className="col-span-3 flex justify-end">
            <Button type="submit" size="sm" isLoading={create.isPending}>
              Создать счёт
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// -----------------------------------------------------------------------------

interface PayForm {
  invoice_id: string;
  amount: string;
  paid_at: string;
  method: PaymentMethod;
  reference: string;
  notes: string;
}

function PaymentForm({
  tenantId,
  defaultInvoiceId,
}: {
  tenantId: string;
  defaultInvoiceId: string;
}): JSX.Element {
  const record = useRecordPayment();
  const [topError, setTopError] = useState<string | null>(null);
  const [recordedAmount, setRecordedAmount] = useState<string | null>(null);
  const now = new Date().toISOString().slice(0, 16);

  const form = useForm<PayForm>({
    defaultValues: {
      invoice_id: defaultInvoiceId,
      amount: "",
      paid_at: now,
      method: "bank_transfer",
      reference: "",
      notes: "",
    },
    values: defaultInvoiceId
      ? {
          invoice_id: defaultInvoiceId,
          amount: "",
          paid_at: now,
          method: "bank_transfer",
          reference: "",
          notes: "",
        }
      : undefined,
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (!values.invoice_id) {
      form.setError("invoice_id", { message: "Введите ID счёта" });
      return;
    }
    if (Number(values.amount) <= 0) {
      form.setError("amount", { message: "Сумма больше 0" });
      return;
    }
    setTopError(null);
    try {
      const p = await record.mutateAsync({
        tenantId,
        invoiceId: values.invoice_id,
        payload: {
          amount: values.amount,
          paid_at: new Date(values.paid_at).toISOString(),
          method: values.method,
          reference: values.reference.trim() || null,
          notes: values.notes.trim() || null,
        },
      });
      setRecordedAmount(`${Number(p.amount).toFixed(2)} ${p.currency}`);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось записать платёж"));
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>3. Платёж</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="grid grid-cols-3 gap-3">
          <div className="col-span-3">
            <Label htmlFor="invoice_id">ID счёта</Label>
            <Input
              id="invoice_id"
              placeholder="UUID счёта"
              invalid={Boolean(form.formState.errors.invoice_id)}
              {...form.register("invoice_id")}
            />
            <FormError>{form.formState.errors.invoice_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="pay_amount">Сумма</Label>
            <Input
              id="pay_amount"
              type="text"
              inputMode="decimal"
              invalid={Boolean(form.formState.errors.amount)}
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="paid_at">Дата оплаты</Label>
            <Input id="paid_at" type="datetime-local" {...form.register("paid_at")} />
          </div>
          <div>
            <Label htmlFor="method">Способ</Label>
            <Select id="method" {...form.register("method")}>
              {paymentMethodOptions.map((m) => (
                <option key={m} value={m}>
                  {paymentMethodLabel[m]}
                </option>
              ))}
            </Select>
          </div>
          <div className="col-span-2">
            <Label htmlFor="reference">Референс</Label>
            <Input id="reference" {...form.register("reference")} />
          </div>
          <div>
            <Label htmlFor="pay_notes">Заметки</Label>
            <Textarea id="pay_notes" rows={1} {...form.register("notes")} />
          </div>
          {topError && (
            <p className="col-span-3 text-sm text-danger">{topError}</p>
          )}
          {recordedAmount && (
            <p className="col-span-3 text-xs text-success-foreground">
              ✅ Платёж записан на {recordedAmount}. Статус счёта обновлён автоматически.
            </p>
          )}
          <div className="col-span-3 flex justify-end">
            <Button type="submit" size="sm" isLoading={record.isPending}>
              Записать платёж
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
