import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import {
  DEFAULT_POS_PAYMENT_METHODS,
  POS_PAYMENT_METHODS,
  normalizePosPaymentMethods,
  type PosPaymentMethod,
} from "@/features/foundation/paymentSettings";
import { useTenantSettingsQuery, useUpdateTenantSettings } from "@/features/foundation/queries";
import {
  type RefundReasonMode,
  type TenantSettings,
  type TenantSettingsUpdatePayload,
} from "@/features/foundation/types";

import { SettingRow, SettingsNotice, SettingsSectionHeader } from "./SettingsPrimitives";

export type OwnerSettingsSection = "pharmacy" | "sales" | "inventory" | "reports";

const schema = z
  .object({
    yellow: z.number().int().min(1).max(24),
    orange: z.number().int().min(1).max(24),
    red: z.number().int().min(1).max(24),
    refund_reason_mode: z.enum(["required", "required_with_text", "optional", "off"]),
    session_admin_minutes: z.number().int().min(30).max(1440),
    session_pos_minutes: z.number().int().min(30).max(1440),
    draft_sale_lifetime_min: z.number().int().min(5).max(240),
    prescription_warning_text: z.string().max(1000),
    pos_payment_methods: z.array(z.enum(POS_PAYMENT_METHODS)).min(1),
    pos_mixed_payment_enabled: z.boolean(),
    report_timezone: z.literal("Asia/Dushanbe"),
  })
  .refine((value) => value.yellow >= value.orange && value.orange >= value.red, {
    message: "Должно быть: жёлтый ≥ оранжевый ≥ красный",
    path: ["red"],
  });

type FormValues = z.infer<typeof schema>;

const defaults: FormValues = {
  yellow: 12,
  orange: 6,
  red: 3,
  refund_reason_mode: "optional",
  session_admin_minutes: 60,
  session_pos_minutes: 480,
  draft_sale_lifetime_min: 30,
  prescription_warning_text: "",
  pos_payment_methods: [...DEFAULT_POS_PAYMENT_METHODS],
  pos_mixed_payment_enabled: true,
  report_timezone: "Asia/Dushanbe",
};

const sectionFields: Record<OwnerSettingsSection, readonly (keyof FormValues)[]> = {
  pharmacy: [
    "session_admin_minutes",
    "session_pos_minutes",
    "draft_sale_lifetime_min",
    "prescription_warning_text",
  ],
  sales: ["refund_reason_mode", "pos_payment_methods", "pos_mixed_payment_enabled"],
  inventory: ["yellow", "orange", "red"],
  reports: ["report_timezone"],
};

const sectionImpact: Record<OwnerSettingsSection, string> = {
  pharmacy: "Изменения действуют для всех сотрудников и касс аптеки.",
  sales: "Способы оплаты и возвраты обновятся на всех кассах после синхронизации.",
  inventory: "Пороги изменят предупреждения о сроках во всех точках аптеки.",
  reports: "Изменения применятся к новым отчётам всей аптеки.",
};

const sectionSavedLabel: Record<OwnerSettingsSection, string> = {
  pharmacy: "Рабочие правила сохранены.",
  sales: "Правила оплаты и возвратов сохранены.",
  inventory: "Пороги срока годности сохранены.",
  reports: "Настройки отчётов сохранены.",
};

const sectionTitle: Record<OwnerSettingsSection, string> = {
  pharmacy: "Рабочие правила",
  sales: "Оплата и возвраты",
  inventory: "Склад и сроки",
  reports: "Отчёты и рабочий день",
};

const refundModes: ReadonlyArray<{ value: RefundReasonMode; label: string }> = [
  { value: "required", label: "Обязательна" },
  { value: "required_with_text", label: "Обязательна с комментарием" },
  { value: "optional", label: "По желанию" },
  { value: "off", label: "Не запрашивать" },
];

const paymentLabels: Record<PosPaymentMethod, string> = {
  cash: "Наличные",
  card: "Карта",
  qr: "QR-код",
};

export function OwnerSettingsPanel({ section }: { section: OwnerSettingsSection }): JSX.Element {
  const settings = useTenantSettingsQuery();
  const update = useUpdateTenantSettings();
  const [topError, setTopError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const form = useForm<FormValues>({ defaultValues: defaults });
  const sectionIsDirty = sectionFields[section].some((field) =>
    Boolean(form.formState.dirtyFields[field]),
  );

  useEffect(() => {
    if (!settings.data) return;
    form.reset(formValuesFromSettings(settings.data));
  }, [form, settings.data]);

  const onSubmit = form.handleSubmit(async (values) => {
    if (!settings.data || !sectionIsDirty) return;
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string") {
          form.setError(field as keyof FormValues, { message: issue.message });
        }
      }
      return;
    }
    const data = parsed.data;
    setTopError(null);
    setSaved(false);
    try {
      await update.mutateAsync({
        expected_version: settings.data.version,
        ...tenantSettingsPatchForSection(section, data),
      });
      form.reset(data);
      setSaved(true);
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось сохранить настройки аптеки"));
    }
  });

  if (settings.isLoading) {
    return <p className="py-8 text-sm text-foreground-muted">Загрузка настроек аптеки…</p>;
  }

  if (settings.error || !settings.data) {
    return (
      <div className="space-y-3">
        <SettingsSectionHeader title={sectionTitle[section]} ownerOnly />
        <SettingsNotice tone="danger">
          {describeApiError(settings.error, "Не удалось загрузить настройки аптеки")}
        </SettingsNotice>
        <Button type="button" variant="secondary" onClick={() => void settings.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {section === "pharmacy" ? <PharmacyFields form={form} /> : null}
      {section === "sales" ? <SalesFields form={form} /> : null}
      {section === "inventory" ? <InventoryFields form={form} /> : null}
      {section === "reports" ? <ReportFields form={form} /> : null}

      {topError ? <SettingsNotice tone="danger">{topError}</SettingsNotice> : null}
      {saved ? <SettingsNotice tone="success">{sectionSavedLabel[section]}</SettingsNotice> : null}

      <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-surface py-3">
        <div className="text-xs text-foreground-muted">
          <p>{sectionImpact[section]}</p>
          <p>Сохранение создаст запись в журнале аудита.</p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            disabled={!sectionIsDirty || update.isPending}
            onClick={() => settings.data && form.reset(formValuesFromSettings(settings.data))}
          >
            Отменить
          </Button>
          <Button type="submit" disabled={!sectionIsDirty} isLoading={update.isPending}>
            Сохранить изменения
          </Button>
        </div>
      </div>
    </form>
  );
}

function tenantSettingsPatchForSection(
  section: OwnerSettingsSection,
  data: FormValues,
): Omit<TenantSettingsUpdatePayload, "expected_version"> {
  switch (section) {
    case "pharmacy":
      return {
        session_admin_minutes: data.session_admin_minutes,
        session_pos_minutes: data.session_pos_minutes,
        draft_sale_lifetime_min: data.draft_sale_lifetime_min,
        prescription_warning_text: data.prescription_warning_text,
      };
    case "sales":
      return {
        refund_reason_mode: data.refund_reason_mode,
        pos_payment_methods: data.pos_payment_methods,
        pos_mixed_payment_enabled: data.pos_mixed_payment_enabled,
      };
    case "inventory":
      return {
        expiry_thresholds: {
          yellow: data.yellow,
          orange: data.orange,
          red: data.red,
        },
      };
    case "reports":
      return { report_timezone: data.report_timezone };
  }
}

function formValuesFromSettings(settings: TenantSettings): FormValues {
  return {
    yellow: settings.expiry_thresholds.yellow,
    orange: settings.expiry_thresholds.orange,
    red: settings.expiry_thresholds.red,
    refund_reason_mode: settings.refund_reason_mode,
    session_admin_minutes: settings.session_admin_minutes,
    session_pos_minutes: settings.session_pos_minutes,
    draft_sale_lifetime_min: settings.draft_sale_lifetime_min,
    prescription_warning_text: settings.prescription_warning_text,
    pos_payment_methods: normalizePosPaymentMethods(settings.pos_payment_methods),
    pos_mixed_payment_enabled: settings.pos_mixed_payment_enabled,
    report_timezone: "Asia/Dushanbe",
  };
}

type SettingsForm = ReturnType<typeof useForm<FormValues>>;

function PharmacyFields({ form }: { form: SettingsForm }): JSX.Element {
  return (
    <div>
      <SettingsSectionHeader
        title="Рабочие правила"
        description="Общие правила сессий, черновиков кассы и рецептурных предупреждений."
        ownerOnly
      />
      <SettingRow
        title="Сессия управления"
        description="Время бездействия до повторного входа в административные разделы."
      >
        <NumberField
          id="session-admin-minutes"
          accessibleLabel="Сессия управления, минут"
          suffix="мин"
          min={30}
          max={1440}
          error={form.formState.errors.session_admin_minutes?.message}
          register={form.register("session_admin_minutes", { valueAsNumber: true })}
        />
      </SettingRow>
      <SettingRow title="Сессия кассы">
        <NumberField
          id="session-pos-minutes"
          accessibleLabel="Сессия кассы, минут"
          suffix="мин"
          min={30}
          max={1440}
          error={form.formState.errors.session_pos_minutes?.message}
          register={form.register("session_pos_minutes", { valueAsNumber: true })}
        />
      </SettingRow>
      <SettingRow
        title="Срок хранения черновика"
        description="Черновик без изменений будет удалён после указанного времени."
      >
        <NumberField
          id="draft-sale-lifetime"
          accessibleLabel="Срок хранения черновика, минут"
          suffix="мин"
          min={5}
          max={240}
          error={form.formState.errors.draft_sale_lifetime_min?.message}
          register={form.register("draft_sale_lifetime_min", { valueAsNumber: true })}
        />
      </SettingRow>
      <SettingRow title="Предупреждение по рецепту" className="md:items-start">
        <div className="w-full sm:w-[32rem]">
          <Label htmlFor="prescription-warning" className="sr-only">
            Текст предупреждения по рецепту
          </Label>
          <Textarea
            id="prescription-warning"
            maxLength={1000}
            {...form.register("prescription_warning_text")}
          />
          <FormError>{form.formState.errors.prescription_warning_text?.message}</FormError>
        </div>
      </SettingRow>
    </div>
  );
}

function SalesFields({ form }: { form: SettingsForm }): JSX.Element {
  return (
    <div>
      <SettingsSectionHeader
        title="Оплата и возвраты"
        description="Способы оплаты и обязательные правила кассовых операций."
        ownerOnly
      />
      <Controller
        control={form.control}
        name="pos_payment_methods"
        render={({ field, fieldState }) => (
          <SettingRow
            title="Доступные способы оплаты"
            description="Хотя бы один способ должен оставаться включённым."
          >
            <div className="space-y-2">
              <div className="flex flex-wrap justify-start gap-2 md:justify-end">
                {POS_PAYMENT_METHODS.map((method) => {
                  const selected = field.value.includes(method);
                  return (
                    <button
                      key={method}
                      type="button"
                      aria-pressed={selected}
                      disabled={selected && field.value.length === 1}
                      onClick={() => {
                        const next = selected
                          ? field.value.filter((item) => item !== method)
                          : [...field.value, method];
                        field.onChange(POS_PAYMENT_METHODS.filter((item) => next.includes(item)));
                      }}
                      className={`min-h-[var(--control-height-md)] rounded-md border px-4 text-sm font-medium ${
                        selected
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border bg-surface text-foreground-secondary"
                      } disabled:cursor-not-allowed disabled:opacity-80`}
                    >
                      {paymentLabels[method]}
                    </button>
                  );
                })}
              </div>
              <FormError>{fieldState.error?.message}</FormError>
            </div>
          </SettingRow>
        )}
      />
      <SettingRow
        title="Смешанная оплата"
        description="Один чек можно разделить между несколькими способами оплаты."
      >
        <Switch label="Разрешить" {...form.register("pos_mixed_payment_enabled")} />
      </SettingRow>
      <SettingRow title="Причина возврата">
        <Select className="w-full sm:w-[22rem]" {...form.register("refund_reason_mode")}>
          {refundModes.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </Select>
      </SettingRow>
      <SettingRow
        title="Просроченные препараты"
        description="Это обязательный системный инвариант и его нельзя отключить."
      >
        <span className="text-sm font-semibold text-success-foreground">Продажа запрещена</span>
      </SettingRow>
      <SettingsNotice>
        Новые правила применяются к следующим операциям после получения кассой актуальной
        конфигурации.
      </SettingsNotice>
    </div>
  );
}

function InventoryFields({ form }: { form: SettingsForm }): JSX.Element {
  return (
    <div>
      <SettingsSectionHeader
        title="Склад и сроки"
        description="Пороги предупреждений о приближении срока годности."
        ownerOnly
      />
      <div className="grid gap-4 py-5 sm:grid-cols-3">
        <ThresholdField
          id="expiry-yellow"
          label="Раннее предупреждение"
          tone="bg-warning-subtle text-warning-foreground"
          error={form.formState.errors.yellow?.message}
          register={form.register("yellow", { valueAsNumber: true })}
        />
        <ThresholdField
          id="expiry-orange"
          label="Требует внимания"
          tone="bg-[#fff1e6] text-[#9a4a11]"
          error={form.formState.errors.orange?.message}
          register={form.register("orange", { valueAsNumber: true })}
        />
        <ThresholdField
          id="expiry-red"
          label="Критический срок"
          tone="bg-danger-subtle text-danger-foreground"
          error={form.formState.errors.red?.message}
          register={form.register("red", { valueAsNumber: true })}
        />
      </div>
      <SettingsNotice>
        Значения задаются в месяцах до даты окончания срока годности и должны идти по убыванию.
      </SettingsNotice>
    </div>
  );
}

function ReportFields({ form }: { form: SettingsForm }): JSX.Element {
  return (
    <div>
      <SettingsSectionHeader
        title="Отчёты и рабочий день"
        description="Единая временная зона для рабочих дней, чеков и аналитики аптеки."
        ownerOnly
      />
      <SettingRow title="Часовой пояс отчётов">
        <Select className="w-full sm:w-[24rem]" {...form.register("report_timezone")}>
          <option value="Asia/Dushanbe">Душанбе · UTC+5</option>
        </Select>
      </SettingRow>
      <SettingRow title="Валюта">
        <span className="text-sm font-medium text-foreground">Сомони (TJS)</span>
      </SettingRow>
      <SettingsNotice>
        Валюта пилота фиксирована. Смена валюты существующей аптеки запрещена, чтобы не искажать
        продажи и отчёты.
      </SettingsNotice>
    </div>
  );
}

function NumberField({
  id,
  accessibleLabel,
  suffix,
  min,
  max,
  error,
  register,
}: {
  id: string;
  accessibleLabel: string;
  suffix: string;
  min: number;
  max: number;
  error?: string;
  register: ReturnType<SettingsForm["register"]>;
}): JSX.Element {
  return (
    <div className="w-full sm:w-44">
      <Label htmlFor={id} className="sr-only">
        {accessibleLabel}
      </Label>
      <div className="flex items-center gap-2">
        <Input id={id} type="number" min={min} max={max} invalid={Boolean(error)} {...register} />
        <span className="text-sm text-foreground-muted">{suffix}</span>
      </div>
      <FormError>{error}</FormError>
    </div>
  );
}

function ThresholdField({
  id,
  label,
  tone,
  error,
  register,
}: {
  id: string;
  label: string;
  tone: string;
  error?: string;
  register: ReturnType<SettingsForm["register"]>;
}): JSX.Element {
  return (
    <div className="rounded-md border border-border bg-background p-4">
      <span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${tone}`}>
        {label}
      </span>
      <div className="mt-3 flex items-center gap-2">
        <Label htmlFor={id} className="sr-only">
          {label}, месяцев
        </Label>
        <Input id={id} type="number" min={1} max={24} invalid={Boolean(error)} {...register} />
        <span className="text-sm text-foreground-muted">мес.</span>
      </div>
      <FormError>{error}</FormError>
    </div>
  );
}
