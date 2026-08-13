import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { createOperationId } from "./operationId";
import { minimumPricingLocalInput, parsePricingLocalInput } from "./pricingTime";
import {
  useActivatePlatformPricingPrice,
  useCancelPlatformPricingPrice,
  useCreatePlatformPricingPlan,
  useCreatePlatformPricingPrice,
  useSchedulePlatformPricingPrice,
} from "./queries";
import {
  type PlatformPricingPlan,
  type PlatformPricingVersion,
  type PricingCancellationReason,
} from "./types";

export type PricingCommandTarget =
  | { kind: "create-plan" }
  | { kind: "create-price"; plan: PlatformPricingPlan }
  | { kind: "schedule"; plan: PlatformPricingPlan; version: PlatformPricingVersion }
  | { kind: "activate"; plan: PlatformPricingPlan; version: PlatformPricingVersion }
  | { kind: "cancel"; plan: PlatformPricingPlan; version: PlatformPricingVersion };

interface Props {
  target: PricingCommandTarget | null;
  online: boolean;
  onClose: () => void;
  onCompleted: (message: string) => void;
  onRefreshRequired: (message: string) => void;
}

export function PricingCommandModal({
  target,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Props): JSX.Element | null {
  if (!target) return null;
  if (target.kind === "create-plan") {
    return (
      <CreatePlanModal
        open
        online={online}
        onClose={onClose}
        onCompleted={onCompleted}
        onRefreshRequired={onRefreshRequired}
      />
    );
  }
  if (target.kind === "create-price") {
    return (
      <CreatePriceModal
        open
        online={online}
        plan={target.plan}
        onClose={onClose}
        onCompleted={onCompleted}
        onRefreshRequired={onRefreshRequired}
      />
    );
  }
  if (target.kind === "schedule") {
    return (
      <SchedulePriceModal
        open
        online={online}
        plan={target.plan}
        version={target.version}
        onClose={onClose}
        onCompleted={onCompleted}
        onRefreshRequired={onRefreshRequired}
      />
    );
  }
  if (target.kind === "activate") {
    return (
      <ActivatePriceModal
        open
        online={online}
        plan={target.plan}
        version={target.version}
        onClose={onClose}
        onCompleted={onCompleted}
        onRefreshRequired={onRefreshRequired}
      />
    );
  }
  return (
    <CancelPriceModal
      open
      online={online}
      plan={target.plan}
      version={target.version}
      onClose={onClose}
      onCompleted={onCompleted}
      onRefreshRequired={onRefreshRequired}
    />
  );
}

const planSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^[a-z][a-z0-9_]{2,63}$/, "Латиница: от 3 символов, без пробелов"),
  name: z.string().trim().min(2, "Минимум 2 символа").max(160, "Не более 160 символов"),
  description: z.string().trim().max(2000, "Не более 2000 символов"),
});

type PlanForm = z.infer<typeof planSchema>;

function CreatePlanModal({
  open,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Omit<Props, "target"> & { open: boolean }): JSX.Element {
  const mutation = useCreatePlatformPricingPlan();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<PlanForm>({ defaultValues: { code: "", name: "", description: "" } });

  useEffect(() => {
    if (!open) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({ code: "", name: "", description: "" });
  }, [form, open]);

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError(offlineCommandMessage);
      return;
    }
    const parsed = planSchema.safeParse(values);
    if (!applyIssues(parsed, form.setError)) return;
    setTopError(null);
    try {
      await mutation.mutateAsync({
        operation_id: operationId,
        code: parsed.data.code,
        name: parsed.data.name,
        description: parsed.data.description || null,
      });
      onCompleted("Тариф создан. Добавьте первую версию цены.");
    } catch (error) {
      handleCommandError(
        error,
        "Не удалось создать тариф.",
        setTopError,
        onClose,
        onRefreshRequired,
      );
    }
  });

  return (
    <Modal open={open} onClose={() => !mutation.isPending && onClose()} title="Новый тариф">
      <form className="space-y-4" noValidate onSubmit={submit}>
        <div>
          <Label htmlFor="pricing-plan-name">Название</Label>
          <Input
            id="pricing-plan-name"
            autoFocus
            invalid={Boolean(form.formState.errors.name)}
            disabled={mutation.isPending}
            {...form.register("name")}
          />
          <FormError>{form.formState.errors.name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="pricing-plan-code">Системный код</Label>
          <Input
            id="pricing-plan-code"
            placeholder="business"
            autoComplete="off"
            invalid={Boolean(form.formState.errors.code)}
            disabled={mutation.isPending}
            {...form.register("code", { setValueAs: (value: string) => value.toLowerCase() })}
          />
          <p className="mt-1 text-xs text-foreground-muted">
            Постоянный идентификатор: латинские буквы, цифры и знак подчёркивания.
          </p>
          <FormError>{form.formState.errors.code?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="pricing-plan-description">Описание</Label>
          <Textarea
            id="pricing-plan-description"
            rows={3}
            invalid={Boolean(form.formState.errors.description)}
            disabled={mutation.isPending}
            {...form.register("description")}
          />
          <FormError>{form.formState.errors.description?.message}</FormError>
        </div>
        <CommandError message={topError} />
        <ModalActions
          pending={mutation.isPending}
          enabled={online}
          confirm="Создать тариф"
          onCancel={onClose}
        />
      </form>
    </Modal>
  );
}

const moneyPattern = /^\d{1,12}(?:[.,]\d{1,2})?$/;
const discountPattern = /^(?:\d|[1-9]\d)(?:[.,]\d{1,2})?$/;
const priceSchema = z
  .object({
    monthly_price_per_branch: z
      .string()
      .trim()
      .regex(moneyPattern, "Введите сумму с точностью до 2 знаков"),
    annual_discount_pct: z.string().trim().regex(discountPattern, "Введите значение от 0 до 99,99"),
    audience: z.enum(["default", "new_customers"]),
    notice_days: z.coerce.number().int().min(0).max(365),
    change_reason: z
      .string()
      .trim()
      .min(10, "Опишите причину подробнее, минимум 10 символов")
      .max(1000, "Не более 1000 символов"),
  })
  .superRefine((values, context) => {
    if (values.audience === "default" && values.notice_days < 30) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["notice_days"],
        message: "Для действующих клиентов требуется минимум 30 дней",
      });
    }
  });

type PriceForm = z.infer<typeof priceSchema>;

function CreatePriceModal({
  open,
  online,
  plan,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Omit<Props, "target"> & { open: boolean; plan: PlatformPricingPlan }): JSX.Element {
  const mutation = useCreatePlatformPricingPrice();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<PriceForm>({
    defaultValues: {
      monthly_price_per_branch: "",
      annual_discount_pct: "20",
      audience: "default",
      notice_days: 30,
      change_reason: "",
    },
  });
  const audience = form.watch("audience");

  useEffect(() => {
    if (!open) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({
      monthly_price_per_branch: "",
      annual_discount_pct: "20",
      audience: "default",
      notice_days: 30,
      change_reason: "",
    });
  }, [form, open, plan.plan_id]);

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError(offlineCommandMessage);
      return;
    }
    const parsed = priceSchema.safeParse(values);
    if (!applyIssues(parsed, form.setError)) return;
    setTopError(null);
    try {
      await mutation.mutateAsync({
        planId: plan.plan_id,
        payload: {
          operation_id: operationId,
          monthly_price_per_branch: normalizeDecimal(parsed.data.monthly_price_per_branch),
          annual_discount_pct: normalizeDecimal(parsed.data.annual_discount_pct),
          audience: parsed.data.audience,
          notice_days: parsed.data.notice_days,
          change_reason: parsed.data.change_reason,
        },
      });
      onCompleted("Черновик цены создан и ожидает независимого согласования.");
    } catch (error) {
      handleCommandError(
        error,
        "Не удалось создать черновик цены.",
        setTopError,
        onClose,
        onRefreshRequired,
      );
    }
  });

  return (
    <Modal
      open={open}
      onClose={() => !mutation.isPending && onClose()}
      title={`Новая цена: ${plan.name}`}
    >
      <form className="space-y-4" noValidate onSubmit={submit}>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="pricing-monthly-price">Цена за точку в месяц, TJS</Label>
            <Input
              id="pricing-monthly-price"
              inputMode="decimal"
              autoFocus
              invalid={Boolean(form.formState.errors.monthly_price_per_branch)}
              disabled={mutation.isPending}
              {...form.register("monthly_price_per_branch")}
            />
            <FormError>{form.formState.errors.monthly_price_per_branch?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="pricing-annual-discount">Скидка при оплате за год, %</Label>
            <Input
              id="pricing-annual-discount"
              inputMode="decimal"
              invalid={Boolean(form.formState.errors.annual_discount_pct)}
              disabled={mutation.isPending}
              {...form.register("annual_discount_pct")}
            />
            <FormError>{form.formState.errors.annual_discount_pct?.message}</FormError>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="pricing-audience">Для кого действует</Label>
            <Select
              id="pricing-audience"
              disabled={mutation.isPending}
              {...form.register("audience")}
            >
              <option value="default">Для всех клиентов</option>
              <option value="new_customers">Только для новых клиентов</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="pricing-notice-days">Срок уведомления, дней</Label>
            <Input
              id="pricing-notice-days"
              type="number"
              min={audience === "default" ? 30 : 0}
              max={365}
              invalid={Boolean(form.formState.errors.notice_days)}
              disabled={mutation.isPending}
              {...form.register("notice_days")}
            />
            <FormError>{form.formState.errors.notice_days?.message}</FormError>
          </div>
        </div>
        <div>
          <Label htmlFor="pricing-change-reason">Обоснование цены</Label>
          <Textarea
            id="pricing-change-reason"
            rows={4}
            placeholder="Почему вводится эта цена и для какой коммерческой задачи"
            invalid={Boolean(form.formState.errors.change_reason)}
            disabled={mutation.isPending}
            {...form.register("change_reason")}
          />
          <FormError>{form.formState.errors.change_reason?.message}</FormError>
        </div>
        <p className="rounded-md border border-info/25 bg-info-subtle p-3 text-sm text-info-foreground">
          После сохранения другой сотрудник с правом управления тарифами должен проверить и
          запланировать публикацию.
        </p>
        <CommandError message={topError} />
        <ModalActions
          pending={mutation.isPending}
          enabled={online}
          confirm="Создать черновик"
          onCancel={onClose}
        />
      </form>
    </Modal>
  );
}

const scheduleSchema = z.object({
  effective_from: z.string().min(1, "Укажите дату и время"),
});

type ScheduleForm = z.infer<typeof scheduleSchema>;

function SchedulePriceModal({
  open,
  online,
  plan,
  version,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Omit<Props, "target"> & {
  open: boolean;
  plan: PlatformPricingPlan;
  version: PlatformPricingVersion;
}): JSX.Element {
  const mutation = useSchedulePlatformPricingPrice();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const minimumDate = minimumPricingLocalInput(version.notice_days);
  const form = useForm<ScheduleForm>({ defaultValues: { effective_from: minimumDate } });

  useEffect(() => {
    if (!open) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({ effective_from: minimumPricingLocalInput(version.notice_days) });
  }, [form, open, version.notice_days, version.price_version_id]);

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError(offlineCommandMessage);
      return;
    }
    const parsed = scheduleSchema.safeParse(values);
    if (!applyIssues(parsed, form.setError)) return;
    const effective = parsePricingLocalInput(parsed.data.effective_from);
    if (!effective) {
      form.setError("effective_from", { message: "Некорректная дата" });
      return;
    }
    const minimum = parsePricingLocalInput(minimumPricingLocalInput(version.notice_days));
    if (!minimum || effective.getTime() < minimum.getTime()) {
      form.setError("effective_from", {
        message: `Дата должна учитывать срок уведомления: ${version.notice_days} дней`,
      });
      return;
    }
    setTopError(null);
    try {
      await mutation.mutateAsync({
        priceId: version.price_version_id,
        payload: {
          operation_id: operationId,
          expected_row_version: version.row_version,
          effective_from: effective.toISOString(),
        },
      });
      onCompleted("Цена согласована и запланирована к публикации.");
    } catch (error) {
      handleCommandError(
        error,
        "Не удалось согласовать цену.",
        setTopError,
        onClose,
        onRefreshRequired,
      );
    }
  });

  return (
    <Modal open={open} onClose={() => !mutation.isPending && onClose()} title="Согласовать цену">
      <form className="space-y-4" noValidate onSubmit={submit}>
        <PriceSummary plan={plan} version={version} />
        <div>
          <Label htmlFor="pricing-effective-from">Дата и время начала действия</Label>
          <Input
            id="pricing-effective-from"
            type="datetime-local"
            min={minimumDate}
            invalid={Boolean(form.formState.errors.effective_from)}
            disabled={mutation.isPending}
            {...form.register("effective_from")}
          />
          <p className="mt-1 text-xs text-foreground-muted">
            Часовой пояс Душанбе. Минимальный срок уведомления: {version.notice_days} дней.
          </p>
          <FormError>{form.formState.errors.effective_from?.message}</FormError>
        </div>
        <p className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-foreground-secondary">
          Вы подтверждаете проверку цены, аудитории, скидки и обоснования. Автор черновика не может
          согласовать собственную цену.
        </p>
        <CommandError message={topError} />
        <ModalActions
          pending={mutation.isPending}
          enabled={online}
          confirm="Согласовать"
          onCancel={onClose}
        />
      </form>
    </Modal>
  );
}

function ActivatePriceModal({
  open,
  online,
  plan,
  version,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Omit<Props, "target"> & {
  open: boolean;
  plan: PlatformPricingPlan;
  version: PlatformPricingVersion;
}): JSX.Element {
  const mutation = useActivatePlatformPricingPrice();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setOperationId(createOperationId());
    setTopError(null);
  }, [open, version.price_version_id]);

  const activate = async () => {
    if (!online) {
      setTopError(offlineCommandMessage);
      return;
    }
    setTopError(null);
    try {
      await mutation.mutateAsync({
        priceId: version.price_version_id,
        payload: { operation_id: operationId, expected_row_version: version.row_version },
      });
      onCompleted("Цена активирована. Предыдущая цена этой аудитории перенесена в архив.");
    } catch (error) {
      handleCommandError(
        error,
        "Не удалось активировать цену.",
        setTopError,
        onClose,
        onRefreshRequired,
      );
    }
  };

  return (
    <Modal open={open} onClose={() => !mutation.isPending && onClose()} title="Активировать цену">
      <div className="space-y-4">
        <PriceSummary plan={plan} version={version} />
        <p className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-foreground-secondary">
          Цена станет действующей немедленно. Текущая активная цена для той же аудитории будет
          автоматически перенесена в архив.
        </p>
        <CommandError message={topError} />
        <ModalActions
          pending={mutation.isPending}
          enabled={online}
          confirm="Активировать"
          onCancel={onClose}
          onConfirm={() => void activate()}
          variant="success"
        />
      </div>
    </Modal>
  );
}

const cancelSchema = z.object({
  reason_code: z.enum([
    "pricing_error",
    "commercial_change",
    "legal_requirement",
    "security_incident",
    "other",
  ]),
  reason: z
    .string()
    .trim()
    .min(10, "Опишите причину подробнее, минимум 10 символов")
    .max(500, "Не более 500 символов"),
});

type CancelForm = z.infer<typeof cancelSchema>;

const cancellationReasonLabel: Record<PricingCancellationReason, string> = {
  pricing_error: "Ошибка в цене",
  commercial_change: "Изменение коммерческих условий",
  legal_requirement: "Требование законодательства",
  security_incident: "Инцидент безопасности",
  other: "Другая причина",
};

function CancelPriceModal({
  open,
  online,
  plan,
  version,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Omit<Props, "target"> & {
  open: boolean;
  plan: PlatformPricingPlan;
  version: PlatformPricingVersion;
}): JSX.Element {
  const mutation = useCancelPlatformPricingPrice();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<CancelForm>({
    defaultValues: { reason_code: "commercial_change", reason: "" },
  });

  useEffect(() => {
    if (!open) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({ reason_code: "commercial_change", reason: "" });
  }, [form, open, version.price_version_id]);

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError(offlineCommandMessage);
      return;
    }
    const parsed = cancelSchema.safeParse(values);
    if (!applyIssues(parsed, form.setError)) return;
    setTopError(null);
    try {
      await mutation.mutateAsync({
        priceId: version.price_version_id,
        payload: {
          operation_id: operationId,
          expected_row_version: version.row_version,
          reason_code: parsed.data.reason_code,
          reason: parsed.data.reason,
        },
      });
      onCompleted("Запланированная цена отменена и сохранена в журнале.");
    } catch (error) {
      handleCommandError(
        error,
        "Не удалось отменить цену.",
        setTopError,
        onClose,
        onRefreshRequired,
      );
    }
  });

  return (
    <Modal
      open={open}
      onClose={() => !mutation.isPending && onClose()}
      title="Отменить публикацию цены"
    >
      <form className="space-y-4" noValidate onSubmit={submit}>
        <PriceSummary plan={plan} version={version} />
        <div>
          <Label htmlFor="pricing-cancel-reason-code">Основание</Label>
          <Select
            id="pricing-cancel-reason-code"
            disabled={mutation.isPending}
            {...form.register("reason_code")}
          >
            {(Object.keys(cancellationReasonLabel) as PricingCancellationReason[]).map((code) => (
              <option key={code} value={code}>
                {cancellationReasonLabel[code]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="pricing-cancel-reason">Комментарий</Label>
          <Textarea
            id="pricing-cancel-reason"
            rows={4}
            invalid={Boolean(form.formState.errors.reason)}
            disabled={mutation.isPending}
            {...form.register("reason")}
          />
          <FormError>{form.formState.errors.reason?.message}</FormError>
        </div>
        <CommandError message={topError} />
        <ModalActions
          pending={mutation.isPending}
          enabled={online}
          confirm="Отменить публикацию"
          onCancel={onClose}
          variant="danger"
        />
      </form>
    </Modal>
  );
}

function PriceSummary({
  plan,
  version,
}: {
  plan: PlatformPricingPlan;
  version: PlatformPricingVersion;
}): JSX.Element {
  return (
    <dl className="grid grid-cols-2 gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div className="col-span-2">
        <dt className="text-xs text-foreground-muted">Тариф</dt>
        <dd className="mt-1 font-semibold text-foreground">{plan.name}</dd>
      </div>
      <div>
        <dt className="text-xs text-foreground-muted">Цена</dt>
        <dd className="mt-1 font-semibold tabular-nums">
          {formatBillingMoney(version.monthly_price_per_branch, version.currency)}
        </dd>
      </div>
      <div>
        <dt className="text-xs text-foreground-muted">Аудитория</dt>
        <dd className="mt-1">{version.audience === "default" ? "Все клиенты" : "Новые клиенты"}</dd>
      </div>
      <div>
        <dt className="text-xs text-foreground-muted">Годовая скидка</dt>
        <dd className="mt-1 tabular-nums">
          {new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(
            Number(version.annual_discount_pct),
          )}{" "}
          %
        </dd>
      </div>
      <div>
        <dt className="text-xs text-foreground-muted">Срок уведомления</dt>
        <dd className="mt-1 tabular-nums">{version.notice_days} дней</dd>
      </div>
      <div className="col-span-2">
        <dt className="text-xs text-foreground-muted">Обоснование</dt>
        <dd className="mt-1 break-words">{version.change_reason ?? "Не указано"}</dd>
      </div>
    </dl>
  );
}

function ModalActions({
  pending,
  enabled,
  confirm,
  onCancel,
  onConfirm,
  variant = "primary",
}: {
  pending: boolean;
  enabled: boolean;
  confirm: string;
  onCancel: () => void;
  onConfirm?: () => void;
  variant?: "primary" | "success" | "danger";
}): JSX.Element {
  return (
    <div className="space-y-3">
      {!enabled ? (
        <p
          className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-warning-foreground"
          role="status"
        >
          {offlineCommandMessage}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="secondary" disabled={pending} onClick={onCancel}>
          Назад
        </Button>
        <Button
          type={onConfirm ? "button" : "submit"}
          variant={variant}
          disabled={!enabled}
          isLoading={pending}
          onClick={onConfirm}
        >
          {confirm}
        </Button>
      </div>
    </div>
  );
}

function CommandError({ message }: { message: string | null }): JSX.Element | null {
  if (!message) return null;
  return (
    <p
      className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm text-danger-foreground"
      role="alert"
    >
      {message}
    </p>
  );
}

const offlineCommandMessage =
  "Нет подключения к серверу. Финансовая команда будет доступна после восстановления связи.";

function handleCommandError(
  error: unknown,
  fallback: string,
  setTopError: (message: string) => void,
  onClose: () => void,
  onRefreshRequired: (message: string) => void,
): void {
  if (isAxiosError(error) && error.response?.status === 409) {
    onClose();
    onRefreshRequired(
      "Данные тарифа уже изменились. Список обновлён — проверьте состояние перед повтором.",
    );
    return;
  }
  setTopError(describeApiError(error, fallback));
}

function applyIssues<T extends z.ZodTypeAny>(
  parsed: z.SafeParseReturnType<unknown, z.infer<T>>,
  setError: (name: never, error: { message: string }) => void,
): parsed is z.SafeParseSuccess<z.infer<T>> {
  if (parsed.success) return true;
  for (const issue of parsed.error.issues) {
    const field = issue.path[0];
    if (typeof field === "string") {
      setError(field as never, { message: issue.message });
    }
  }
  return false;
}

function normalizeDecimal(value: string): string {
  const normalized = value.replace(",", ".");
  const [whole, fraction = ""] = normalized.split(".");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}
