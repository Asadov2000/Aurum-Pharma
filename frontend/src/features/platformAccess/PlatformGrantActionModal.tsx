import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { AxiosError } from "axios";
import { z } from "zod";

import { Badge, Button, FormError, Label, Modal, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { accessKindLabel, accessReasonLabel, platformCapabilityLabel } from "./labels";
import { useApprovePlatformAccessGrant, useRevokePlatformAccessGrant } from "./queries";
import { type PlatformAccessGrant, type PlatformAccessReasonCode } from "./types";

const schema = z.object({
  reason_code: z.enum([
    "platform_staff_onboarding",
    "responsibility_change",
    "security_incident",
    "access_review",
    "other",
  ]),
  reason: z
    .string()
    .trim()
    .min(10, "Опишите основание подробнее, минимум 10 символов")
    .max(500, "Не более 500 символов"),
});

type FormValues = z.infer<typeof schema>;
export type PlatformGrantAction = "approve" | "revoke";

interface Props {
  action: PlatformGrantAction;
  grant: PlatformAccessGrant | null;
  open: boolean;
  onClose: () => void;
  onCompleted: (grant: PlatformAccessGrant) => void;
  onRefreshRequired: (message: string) => void;
}

const reasonCodes = Object.keys(accessReasonLabel) as PlatformAccessReasonCode[];

export function PlatformGrantActionModal({
  action,
  grant,
  open,
  onClose,
  onCompleted,
  onRefreshRequired,
}: Props): JSX.Element | null {
  const approve = useApprovePlatformAccessGrant();
  const revoke = useRevokePlatformAccessGrant();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: {
      reason_code: action === "approve" ? "access_review" : "responsibility_change",
      reason: "",
    },
  });

  useEffect(() => {
    if (!open) return;
    setTopError(null);
    form.reset({
      reason_code: action === "approve" ? "access_review" : "responsibility_change",
      reason: "",
    });
  }, [action, form, grant?.id, open]);

  if (!grant) return null;
  const isPending = approve.isPending || revoke.isPending;

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
      const mutation = action === "approve" ? approve : revoke;
      const result = await mutation.mutateAsync({
        grantId: grant.id,
        payload: { version: grant.version, ...parsed.data },
      });
      onCompleted(result);
      onClose();
    } catch (error) {
      if (error instanceof AxiosError && (!error.response || error.response.status === 409)) {
        onRefreshRequired(
          error.response?.status === 409
            ? "Заявка уже изменилась. Список обновлён, выберите действие заново."
            : "Ответ сервера не получен. Состояние перечитано, проверьте заявку перед новым действием.",
        );
        onClose();
        return;
      }
      setTopError(
        describeApiError(
          error,
          action === "approve" ? "Не удалось подтвердить доступ" : "Не удалось отозвать доступ",
        ),
      );
    }
  });

  const accountName = grant.user_full_name ?? grant.user_email ?? grant.user_id;

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!isPending) onClose();
      }}
      title={action === "approve" ? "Подтвердить доступ" : "Отозвать доступ"}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="space-y-3 rounded-md border border-border bg-background p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">{accountName}</p>
              {grant.user_email && grant.user_full_name && (
                <p className="truncate text-xs text-foreground-muted">{grant.user_email}</p>
              )}
            </div>
            <Badge tone={grant.access_kind === "developer" ? "danger" : "info"}>
              {accessKindLabel[grant.access_kind]}
            </Badge>
          </div>
          <div>
            <p className="text-xs font-medium text-foreground-muted">Возможности</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {grant.capabilities.map((capability) => (
                <Badge key={capability} tone="neutral">
                  {platformCapabilityLabel[capability] ?? capability}
                </Badge>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-foreground-muted">Причина заявки</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-foreground-secondary">
              {grant.request_reason}
            </p>
          </div>
        </div>

        <div>
          <Label htmlFor="platform-access-reason-code">Основание</Label>
          <Select
            id="platform-access-reason-code"
            {...form.register("reason_code")}
            disabled={isPending}
          >
            {reasonCodes.map((code) => (
              <option key={code} value={code}>
                {accessReasonLabel[code]}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.reason_code?.message}</FormError>
        </div>

        <div>
          <Label htmlFor="platform-access-reason">Комментарий</Label>
          <Textarea
            id="platform-access-reason"
            rows={4}
            invalid={Boolean(form.formState.errors.reason)}
            disabled={isPending}
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
          <Button type="button" variant="secondary" disabled={isPending} onClick={onClose}>
            Отмена
          </Button>
          <Button
            type="submit"
            variant={action === "revoke" ? "danger" : "primary"}
            isLoading={isPending}
          >
            {action === "approve" ? "Подтвердить" : "Отозвать"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
