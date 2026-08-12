import { useRef, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import {
  useRevokeSyncNode,
  useStartSyncCredentialRotation,
  useTransitionSyncCredentialRotation,
} from "./queries";
import {
  type SyncMonitoringNode,
  type SyncNodeAction,
  type SyncNodeActionReasonCode,
} from "./types";

const formSchema = z.object({
  confirmation_name: z.string().trim().min(1, "Введите точное имя узла"),
  reason_code: z.enum([
    "routine_maintenance",
    "credential_expiry",
    "security_incident",
    "device_replacement",
    "device_retired",
    "other",
  ]),
  reason: z
    .string()
    .trim()
    .min(10, "Опишите основание подробнее, минимум 10 символов")
    .max(500, "Не более 500 символов"),
  credential_valid_days: z.coerce.number().int().min(1).max(365),
});

type FormValues = z.infer<typeof formSchema>;

const reasonLabel: Record<SyncNodeActionReasonCode, string> = {
  routine_maintenance: "Плановое обслуживание",
  credential_expiry: "Истечение срока ключа",
  security_incident: "Инцидент безопасности",
  device_replacement: "Замена устройства",
  device_retired: "Вывод устройства из эксплуатации",
  other: "Другое",
};

const reasonCodes = Object.keys(reasonLabel) as SyncNodeActionReasonCode[];

const actionConfig: Record<
  SyncNodeAction,
  { title: string; confirm: string; warning: string; defaultReason: SyncNodeActionReasonCode }
> = {
  rotate: {
    title: "Подготовить новый ключ",
    confirm: "Создать новый ключ",
    warning:
      "Текущий ключ продолжит работать. Новый ключ будет показан один раз и должен обратиться к серверу в течение 24 часов.",
    defaultReason: "credential_expiry",
  },
  complete: {
    title: "Завершить замену ключа",
    confirm: "Активировать новый ключ",
    warning:
      "Сервер уже подтвердил новый ключ. После завершения текущий ключ немедленно перестанет работать.",
    defaultReason: "routine_maintenance",
  },
  cancel: {
    title: "Отменить замену ключа",
    confirm: "Отменить замену",
    warning: "Новый ключ перестанет приниматься. Текущий ключ останется без изменений.",
    defaultReason: "routine_maintenance",
  },
  revoke: {
    title: "Отозвать узел",
    confirm: "Отозвать узел",
    warning:
      "Устройство сразу потеряет доступ к синхронизации. Восстановить этот узел после отзыва нельзя.",
    defaultReason: "device_retired",
  },
};

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
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

interface Props {
  action: SyncNodeAction;
  node: SyncMonitoringNode;
  onClose: () => void;
  onCompleted: () => void;
}

export function SyncNodeActionModal({
  action,
  node,
  onClose,
  onCompleted,
}: Props): JSX.Element {
  const config = actionConfig[action];
  const [operationId] = useState(createOperationId);
  const [credential, setCredential] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const submissionInFlight = useRef(false);
  const startRotation = useStartSyncCredentialRotation(node.node_id);
  const transition = useTransitionSyncCredentialRotation(
    node.credential_rotation_id ?? "",
    action === "cancel" ? "cancel" : "complete",
  );
  const revoke = useRevokeSyncNode(node.node_id);
  const form = useForm<FormValues>({
    defaultValues: {
      confirmation_name: "",
      reason_code: config.defaultReason,
      reason: "",
      credential_valid_days: 90,
    },
  });
  const pending = startRotation.isPending || transition.isPending || revoke.isPending;

  const clearAndClose = () => {
    startRotation.reset();
    transition.reset();
    revoke.reset();
    setCredential(null);
    onClose();
  };

  const close = () => {
    if (pending) return;
    clearAndClose();
  };

  const submit = form.handleSubmit(async (values) => {
    if (submissionInFlight.current) return;
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (
          field === "confirmation_name" ||
          field === "reason_code" ||
          field === "reason" ||
          field === "credential_valid_days"
        ) {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }
    if (parsed.data.confirmation_name !== node.display_name) {
      form.setError("confirmation_name", { message: "Имя узла не совпадает" });
      return;
    }

    setTopError(null);
    submissionInFlight.current = true;
    try {
      const common = {
        expected_version: node.lifecycle_version,
        operation_id: operationId,
        confirmation_name: parsed.data.confirmation_name,
        reason_code: parsed.data.reason_code,
        reason: parsed.data.reason,
      };
      if (action === "rotate") {
        const result = await startRotation.mutateAsync({
          ...common,
          credential_valid_days: parsed.data.credential_valid_days,
        });
        onCompleted();
        if (result.credential) {
          setCredential(result.credential);
        } else {
          setTopError(
            "Операция уже была принята, поэтому ключ повторно не показан. Отмените её и создайте новую замену.",
          );
        }
        return;
      }
      if (action === "revoke") {
        await revoke.mutateAsync(common);
      } else {
        await transition.mutateAsync(common);
      }
      onCompleted();
      clearAndClose();
    } catch (error) {
      if (isAxiosError(error) && !error.response) {
        setUncertain(true);
        setTopError(
          "Ответ сервера не получен. Не повторяйте действие: закройте окно и обновите состояние узла.",
        );
        return;
      }
      setTopError(describeApiError(error, `Не удалось выполнить действие «${config.confirm}».`));
    } finally {
      submissionInFlight.current = false;
    }
  });

  const copyCredential = async () => {
    if (!credential) return;
    setTopError(null);
    try {
      await navigator.clipboard.writeText(credential);
      setCopied(true);
    } catch {
      setTopError("Не удалось скопировать ключ. Выделите и скопируйте его вручную.");
    }
  };

  return (
    <Modal open onClose={close} title={config.title} className="max-w-xl">
      {credential ? (
        <div className="space-y-4">
          <p className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-foreground-secondary">
            Ключ показывается только сейчас. Передайте его на нужную кассу по защищённому каналу.
          </p>
          <div>
            <Label htmlFor="sync-new-credential">Новый ключ</Label>
            <Textarea
              id="sync-new-credential"
              value={credential}
              readOnly
              rows={4}
              spellCheck={false}
              className="font-mono text-xs"
            />
          </div>
          {topError && (
            <p role="alert" className="text-sm text-danger">
              {topError}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={copyCredential}>
              {copied ? "Скопировано" : "Копировать ключ"}
            </Button>
            <Button type="button" onClick={close}>
              Готово
            </Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <div className="rounded-md border border-border bg-background p-3">
            <p className="truncate text-sm font-semibold text-foreground">{node.display_name}</p>
            <p className="truncate text-xs text-foreground-muted">
              {node.tenant_name} · {node.branch_name}
            </p>
          </div>
          <p className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-sm text-foreground-secondary">
            {config.warning}
          </p>
          {action === "rotate" && (
            <div>
              <Label htmlFor="sync-credential-valid-days">Срок нового ключа, дней</Label>
              <Input
                id="sync-credential-valid-days"
                type="number"
                min={1}
                max={365}
                disabled={pending || uncertain}
                invalid={Boolean(form.formState.errors.credential_valid_days)}
                {...form.register("credential_valid_days")}
              />
              <FormError>{form.formState.errors.credential_valid_days?.message}</FormError>
            </div>
          )}
          <div>
            <Label htmlFor="sync-node-reason-code">Основание</Label>
            <Select
              id="sync-node-reason-code"
              disabled={pending || uncertain}
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
            <Label htmlFor="sync-node-reason">Комментарий</Label>
            <Textarea
              id="sync-node-reason"
              rows={3}
              disabled={pending || uncertain}
              invalid={Boolean(form.formState.errors.reason)}
              {...form.register("reason")}
            />
            <FormError>{form.formState.errors.reason?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="sync-node-confirmation">
              Для подтверждения введите: {node.display_name}
            </Label>
            <Input
              id="sync-node-confirmation"
              autoComplete="off"
              disabled={pending || uncertain}
              invalid={Boolean(form.formState.errors.confirmation_name)}
              {...form.register("confirmation_name")}
            />
            <FormError>{form.formState.errors.confirmation_name?.message}</FormError>
          </div>
          {topError && (
            <p role="alert" className="text-sm text-danger">
              {topError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={pending} onClick={close}>
              Закрыть
            </Button>
            <Button
              type="submit"
              variant={action === "revoke" ? "danger" : "primary"}
              isLoading={pending}
              disabled={uncertain}
            >
              {config.confirm}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
