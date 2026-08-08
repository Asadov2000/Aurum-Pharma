import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormError,
  Input,
  Label,
  PageHeader,
  Select,
  Switch,
  Textarea,
} from "@/components/ui";

import { describeApiError } from "./errors";
import {
  DEFAULT_POS_PAYMENT_METHODS,
  POS_PAYMENT_METHODS,
  normalizePosPaymentMethods,
  type PosPaymentMethod,
} from "./paymentSettings";
import { useTenantSettingsQuery, useUpdateTenantSettings } from "./queries";
import { type ExpiredSaleMode, type RefundReasonMode } from "./types";

const expiredSaleModes: ExpiredSaleMode[] = ["strict", "warning", "off"];
const expiredSaleLabel: Record<ExpiredSaleMode, string> = {
  strict: "Запрещать",
  warning: "Предупреждать",
  off: "Игнорировать",
};

const refundModes: RefundReasonMode[] = ["required", "required_with_text", "optional", "off"];
const refundModeLabel: Record<RefundReasonMode, string> = {
  required: "Обязательна",
  required_with_text: "Обязательна с комментарием",
  optional: "По желанию",
  off: "Не запрашивать",
};

const paymentMethodCopy: Record<
  PosPaymentMethod,
  { title: string; description: string }
> = {
  cash: {
    title: "Наличные",
    description: "Оплата купюрами и монетами с расчётом сдачи.",
  },
  card: {
    title: "Карта",
    description: "Оплата через банковский терминал.",
  },
  qr: {
    title: "QR-код",
    description: "Ручная фиксация подтверждённой оплаты по QR-коду банка.",
  },
};

const schema = z
  .object({
    yellow: z.number().int().min(1).max(24),
    orange: z.number().int().min(1).max(24),
    red: z.number().int().min(1).max(24),
    expired_sale_mode: z.enum(["strict", "warning", "off"]),
    refund_reason_mode: z.enum(["required", "required_with_text", "optional", "off"]),
    session_admin_minutes: z.number().int().min(30).max(1440),
    session_pos_minutes: z.number().int().min(30).max(1440),
    draft_sale_lifetime_min: z.number().int().min(5).max(240),
    prescription_warning_text: z.string(),
    pos_payment_methods: z
      .array(z.enum(POS_PAYMENT_METHODS))
      .min(1, "Выберите хотя бы один способ оплаты"),
    pos_mixed_payment_enabled: z.boolean(),
  })
  .refine((v) => v.yellow >= v.orange && v.orange >= v.red, {
    message: "Должно быть жёлтый ≥ оранжевый ≥ красный",
    path: ["red"],
  });

type FormValues = z.infer<typeof schema>;

export function SettingsPage(): JSX.Element {
  const { data, isLoading, error } = useTenantSettingsQuery();
  const updateMutation = useUpdateTenantSettings();
  const [topError, setTopError] = useState<string | null>(null);
  const [okBanner, setOkBanner] = useState(false);

  const form = useForm<FormValues>({
    defaultValues: {
      yellow: 12,
      orange: 6,
      red: 3,
      expired_sale_mode: "warning",
      refund_reason_mode: "optional",
      session_admin_minutes: 60,
      session_pos_minutes: 480,
      draft_sale_lifetime_min: 30,
      prescription_warning_text: "",
      pos_payment_methods: [...DEFAULT_POS_PAYMENT_METHODS],
      pos_mixed_payment_enabled: true,
    },
  });

  useEffect(() => {
    if (!data) return;
    form.reset({
      yellow: data.expiry_thresholds.yellow,
      orange: data.expiry_thresholds.orange,
      red: data.expiry_thresholds.red,
      expired_sale_mode: data.expired_sale_mode,
      refund_reason_mode: data.refund_reason_mode,
      session_admin_minutes: data.session_admin_minutes,
      session_pos_minutes: data.session_pos_minutes,
      draft_sale_lifetime_min: data.draft_sale_lifetime_min,
      prescription_warning_text: data.prescription_warning_text,
      pos_payment_methods: normalizePosPaymentMethods(data.pos_payment_methods),
      pos_mixed_payment_enabled: data.pos_mixed_payment_enabled ?? true,
    });
  }, [data, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    setOkBanner(false);
    const d = parsed.data;
    try {
      await updateMutation.mutateAsync({
        expiry_thresholds: { yellow: d.yellow, orange: d.orange, red: d.red },
        expired_sale_mode: d.expired_sale_mode,
        refund_reason_mode: d.refund_reason_mode,
        session_admin_minutes: d.session_admin_minutes,
        session_pos_minutes: d.session_pos_minutes,
        draft_sale_lifetime_min: d.draft_sale_lifetime_min,
        prescription_warning_text: d.prescription_warning_text,
        pos_payment_methods: d.pos_payment_methods,
        pos_mixed_payment_enabled: d.pos_mixed_payment_enabled,
      });
      setOkBanner(true);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить настройки"));
    }
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <PageHeader title="Настройки" />
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="space-y-4">
        <PageHeader title="Настройки" />
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
        >
          {describeApiError(error, "Не удалось загрузить настройки")}
        </div>
        <p className="text-sm text-foreground-muted">
          У учёток developer/administrator нет привязки к тенанту — войдите как owner.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-3xl space-y-6">
      <PageHeader title="Настройки" />

      <Card>
        <CardHeader>
          <CardTitle>Оплата на кассе</CardTitle>
          <p className="text-sm leading-5 text-foreground-muted">
            Кассир увидит только включённые способы. Изменения применяются ко всем кассам аптеки.
          </p>
        </CardHeader>
        <CardContent className="space-y-5">
          <Controller
            control={form.control}
            name="pos_payment_methods"
            render={({ field }) => (
              <div
                role="group"
                aria-labelledby="pos-payment-methods-label"
                aria-describedby="pos-payment-methods-help pos-payment-methods-error"
                className="space-y-3"
              >
                <div>
                  <p
                    id="pos-payment-methods-label"
                    className="text-sm font-medium text-foreground"
                  >
                    Доступные способы
                  </p>
                  <p id="pos-payment-methods-help" className="mt-1 text-xs text-foreground-muted">
                    Минимум один способ должен оставаться включённым.
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {POS_PAYMENT_METHODS.map((method) => {
                    const checked = field.value.includes(method);
                    const isLastEnabled = checked && field.value.length === 1;
                    const copy = paymentMethodCopy[method];
                    return (
                      <div
                        key={method}
                        className="flex min-h-24 flex-col justify-between rounded-lg border border-border bg-surface-raised p-3"
                      >
                        <Switch
                          id={`pos-payment-method-${method}`}
                          label={copy.title}
                          checked={checked}
                          disabled={isLastEnabled}
                          onBlur={field.onBlur}
                          onChange={(event) => {
                            const next = event.target.checked
                              ? [...field.value, method]
                              : field.value.filter((value) => value !== method);
                            field.onChange(POS_PAYMENT_METHODS.filter((value) => next.includes(value)));
                          }}
                          aria-describedby={`pos-payment-method-${method}-description`}
                          className="min-h-11 font-medium text-foreground"
                        />
                        <p
                          id={`pos-payment-method-${method}-description`}
                          className="text-xs leading-5 text-foreground-muted"
                        >
                          {copy.description}
                        </p>
                      </div>
                    );
                  })}
                </div>
                <FormError id="pos-payment-methods-error">
                  {form.formState.errors.pos_payment_methods?.message}
                </FormError>
              </div>
            )}
          />

          <div className="flex min-h-16 flex-col justify-center gap-1 rounded-lg border border-border bg-background px-3 py-2">
            <Switch
              id="pos-mixed-payment-enabled"
              label="Разрешить смешанную оплату"
              className="min-h-11 font-medium text-foreground"
              {...form.register("pos_mixed_payment_enabled")}
            />
            <p className="pl-12 text-xs leading-5 text-foreground-muted">
              Кассир сможет разделить один чек между несколькими способами оплаты.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Сроки годности (месяцы)</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="yellow">Жёлтый</Label>
            <Input
              id="yellow"
              type="number"
              min={1}
              max={24}
              {...form.register("yellow", { valueAsNumber: true })}
            />
          </div>
          <div>
            <Label htmlFor="orange">Оранжевый</Label>
            <Input
              id="orange"
              type="number"
              min={1}
              max={24}
              {...form.register("orange", { valueAsNumber: true })}
            />
          </div>
          <div>
            <Label htmlFor="red">Красный</Label>
            <Input
              id="red"
              type="number"
              min={1}
              max={24}
              invalid={Boolean(form.formState.errors.red)}
              {...form.register("red", { valueAsNumber: true })}
            />
            <FormError>{form.formState.errors.red?.message}</FormError>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Продажа и возврат</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="expired_sale_mode">Просроченные ЛС</Label>
            <Select id="expired_sale_mode" {...form.register("expired_sale_mode")}>
              {expiredSaleModes.map((m) => (
                <option key={m} value={m}>
                  {expiredSaleLabel[m]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="refund_reason_mode">Причина возврата</Label>
            <Select id="refund_reason_mode" {...form.register("refund_reason_mode")}>
              {refundModes.map((m) => (
                <option key={m} value={m}>
                  {refundModeLabel[m]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="prescription_warning_text">Текст предупреждения по рецепту</Label>
            <Textarea
              id="prescription_warning_text"
              {...form.register("prescription_warning_text")}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Сессии</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="session_admin_minutes">Админ-панель (минут)</Label>
            <Input
              id="session_admin_minutes"
              type="number"
              min={30}
              max={1440}
              {...form.register("session_admin_minutes", { valueAsNumber: true })}
            />
          </div>
          <div>
            <Label htmlFor="session_pos_minutes">Касса (минут)</Label>
            <Input
              id="session_pos_minutes"
              type="number"
              min={30}
              max={1440}
              {...form.register("session_pos_minutes", { valueAsNumber: true })}
            />
          </div>
          <div>
            <Label htmlFor="draft_sale_lifetime_min">Жизнь черновика чека (минут)</Label>
            <Input
              id="draft_sale_lifetime_min"
              type="number"
              min={5}
              max={240}
              invalid={Boolean(form.formState.errors.draft_sale_lifetime_min)}
              {...form.register("draft_sale_lifetime_min", { valueAsNumber: true })}
            />
            <FormError>{form.formState.errors.draft_sale_lifetime_min?.message}</FormError>
          </div>
          {/* PIN-режим скрыт до реализации backend-аутентификации по PIN
              (пост-пилот). Поле в настройках сохраняет своё значение в БД. */}
        </CardContent>
      </Card>

      {topError && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
        >
          {topError}
        </div>
      )}
      {okBanner && (
        <div
          role="status"
          className="rounded-lg border border-success/30 bg-success-subtle px-3 py-2 text-sm leading-5 text-success-foreground"
        >
          Настройки сохранены.
        </div>
      )}

      <div className="flex flex-wrap justify-end">
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          Сохранить
        </Button>
      </div>
    </form>
  );
}
