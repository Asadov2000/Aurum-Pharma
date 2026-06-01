import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
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
  Select,
  Switch,
  Textarea,
} from "@/components/ui";

import { describeApiError } from "./errors";
import { useTenantSettingsQuery, useUpdateTenantSettings } from "./queries";
import {
  type ExpiredSaleMode,
  type RefundReasonMode,
} from "./types";

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

const schema = z
  .object({
    yellow: z.number().int().min(1).max(24),
    orange: z.number().int().min(1).max(24),
    red: z.number().int().min(1).max(24),
    expired_sale_mode: z.enum(["strict", "warning", "off"]),
    refund_reason_mode: z.enum(["required", "required_with_text", "optional", "off"]),
    session_admin_minutes: z.number().int().min(30).max(1440),
    session_pos_minutes: z.number().int().min(30).max(1440),
    pin_mode_enabled: z.boolean(),
    draft_sale_lifetime_min: z.number().int().min(5).max(240),
    prescription_warning_text: z.string(),
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
      pin_mode_enabled: false,
      draft_sale_lifetime_min: 30,
      prescription_warning_text: "",
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
      pin_mode_enabled: data.pin_mode_enabled,
      draft_sale_lifetime_min: data.draft_sale_lifetime_min,
      prescription_warning_text: data.prescription_warning_text,
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
        pin_mode_enabled: d.pin_mode_enabled,
        draft_sale_lifetime_min: d.draft_sale_lifetime_min,
        prescription_warning_text: d.prescription_warning_text,
      });
      setOkBanner(true);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить настройки"));
    }
  });

  if (isLoading) {
    return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  }
  if (error || !data) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Настройки</h1>
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить настройки")}
        </p>
        <p className="text-sm text-foreground-muted">
          У учёток developer/administrator нет привязки к тенанту — войдите как owner.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold text-foreground">Настройки</h1>

      <Card>
        <CardHeader>
          <CardTitle>Сроки годности (месяцы)</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-3 gap-4">
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
        <CardContent className="grid grid-cols-2 gap-4">
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
          <div className="col-span-2">
            <Switch
              label="PIN-режим на кассе (переключение продавцов)"
              {...form.register("pin_mode_enabled")}
            />
          </div>
        </CardContent>
      </Card>

      {topError && <p className="text-sm text-danger">{topError}</p>}
      {okBanner && <p className="text-sm text-success-foreground">Настройки сохранены.</p>}

      <Button type="submit" isLoading={form.formState.isSubmitting}>
        Сохранить
      </Button>
    </form>
  );
}
