import { useLayoutEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { usePlatformStaffAccountAction } from "./queries";
import {
  type PlatformAccountAction,
  type PlatformAccountReasonCode,
  type PlatformStaffAccount,
  type PlatformStaffInvitation,
} from "./types";

const schema = z.object({
  reason_code: z.enum([
    "invitation_delivery",
    "responsibility_change",
    "security_incident",
    "access_review",
    "employment_ended",
    "other",
  ]),
  reason: z
    .string()
    .trim()
    .min(10, "Опишите основание подробнее, минимум 10 символов")
    .max(500, "Не более 500 символов"),
});

type FormValues = z.infer<typeof schema>;

const reasonLabel: Record<PlatformAccountReasonCode, string> = {
  invitation_delivery: "Повторная доставка приглашения",
  responsibility_change: "Изменение обязанностей",
  security_incident: "Инцидент безопасности",
  access_review: "Проверка доступа",
  employment_ended: "Завершение работы",
  other: "Другое",
};

const reasonCodes = Object.keys(reasonLabel) as PlatformAccountReasonCode[];

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

const actionConfig: Record<
  PlatformAccountAction,
  { title: string; confirm: string; warning: string; defaultReason: PlatformAccountReasonCode }
> = {
  reinvite: {
    title: "Отправить приглашение повторно",
    confirm: "Создать новую ссылку",
    warning: "Предыдущая ссылка активации сразу перестанет работать.",
    defaultReason: "invitation_delivery",
  },
  block: {
    title: "Заблокировать аккаунт",
    confirm: "Заблокировать",
    warning: "Все активные сессии и платформенные права сотрудника будут немедленно отозваны.",
    defaultReason: "security_incident",
  },
  unblock: {
    title: "Разблокировать аккаунт",
    confirm: "Разблокировать",
    warning: "Ранее отозванные права и сессии не восстановятся. Доступ потребуется выдать заново.",
    defaultReason: "access_review",
  },
  offboard: {
    title: "Вывести сотрудника из команды",
    confirm: "Вывести из команды",
    warning:
      "Действие необратимо: аккаунт, сессии, приглашение и платформенные права будут отключены.",
    defaultReason: "employment_ended",
  },
};

interface Props {
  action: PlatformAccountAction;
  account: PlatformStaffAccount | null;
  open: boolean;
  onClose: () => void;
  onCompleted: (action: PlatformAccountAction) => void;
  onRefreshRequired: (message: string) => void;
}

export function PlatformAccountActionModal({
  action,
  account,
  open,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Props): JSX.Element | null {
  const mutation = usePlatformStaffAccountAction(action);
  const config = actionConfig[action];
  const [topError, setTopError] = useState<string | null>(null);
  const [operationId, setOperationId] = useState(createOperationId);
  const [activationUrl, setActivationUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const form = useForm<FormValues>({
    defaultValues: { reason_code: config.defaultReason, reason: "" },
  });

  // Reset before paint so a fast keyboard user cannot type into a form that
  // a deferred opening effect clears immediately afterwards.
  useLayoutEffect(() => {
    if (!open) return;
    setTopError(null);
    setActivationUrl(null);
    setCopied(false);
    setOperationId(createOperationId());
    form.reset({ reason_code: config.defaultReason, reason: "" });
  }, [account?.user_id, action, config.defaultReason, form, open]);

  if (!account) return null;

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "reason" || field === "reason_code") {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }

    setTopError(null);
    try {
      const result = await mutation.mutateAsync({
        userId: account.user_id,
        payload: {
          version: account.version,
          operation_id: operationId,
          ...parsed.data,
        },
      });
      if (action === "reinvite" && "activation_token" in result && result.activation_token) {
        setActivationUrl(createActivationUrl(result));
        onCompleted(action);
        return;
      }
      onCompleted(action);
      onClose();
    } catch (error) {
      if (isAxiosError(error) && (!error.response || error.response.status === 409)) {
        onRefreshRequired(
          error.response?.status === 409
            ? "Аккаунт уже изменился. Список обновлён, выберите действие заново."
            : "Ответ сервера не получен. Состояние перечитано, проверьте аккаунт перед повтором.",
        );
        onClose();
        return;
      }
      setTopError(describeApiError(error, `Не удалось выполнить действие «${config.confirm}».`));
    }
  });

  const copyActivationUrl = async () => {
    if (!activationUrl) return;
    setTopError(null);
    try {
      await navigator.clipboard.writeText(activationUrl);
      setCopied(true);
    } catch {
      setTopError("Не удалось скопировать ссылку. Выделите и скопируйте её вручную.");
    }
  };

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!mutation.isPending) onClose();
      }}
      title={config.title}
    >
      {activationUrl ? (
        <div className="space-y-4">
          <p className="text-sm text-foreground-secondary">
            Новая одноразовая ссылка создана. Передайте её сотруднику по защищённому каналу.
          </p>
          <div>
            <Label htmlFor="platform-lifecycle-activation-url">Ссылка активации</Label>
            <Input id="platform-lifecycle-activation-url" value={activationUrl} readOnly />
          </div>
          {topError && (
            <p role="alert" className="text-sm text-danger">
              {topError}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={copyActivationUrl}>
              {copied ? "Скопировано" : "Копировать ссылку"}
            </Button>
            <Button type="button" onClick={onClose}>
              Готово
            </Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <div className="rounded-md border border-border bg-background p-3">
            <p className="truncate text-sm font-semibold text-foreground">{account.full_name}</p>
            <p className="truncate text-xs text-foreground-muted">{account.email}</p>
          </div>
          <p className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-foreground-secondary">
            {config.warning}
          </p>
          <div>
            <Label htmlFor="platform-account-reason-code">Основание</Label>
            <Select
              id="platform-account-reason-code"
              disabled={mutation.isPending}
              {...form.register("reason_code")}
            >
              {reasonCodes.map((code) => (
                <option key={code} value={code}>
                  {reasonLabel[code]}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.reason_code?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="platform-account-reason">Комментарий</Label>
            <Textarea
              id="platform-account-reason"
              rows={4}
              invalid={Boolean(form.formState.errors.reason)}
              disabled={mutation.isPending}
              {...form.register("reason")}
            />
            <FormError>{form.formState.errors.reason?.message}</FormError>
          </div>
          {topError && (
            <p role="alert" className="text-sm text-danger">
              {topError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={mutation.isPending}
              onClick={onClose}
            >
              Отмена
            </Button>
            <Button
              type="submit"
              variant={action === "block" || action === "offboard" ? "danger" : "primary"}
              isLoading={mutation.isPending}
            >
              {config.confirm}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function createActivationUrl(invitation: PlatformStaffInvitation): string {
  return `${window.location.origin}/activate-platform#token=${encodeURIComponent(invitation.activation_token ?? "")}`;
}
