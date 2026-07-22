import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "@tanstack/react-router";
import { z } from "zod";

import { Button, FormError, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { activateSupportContext } from "./context";
import { useStartSupportSession, useSupportCapabilities } from "./queries";

const schema = z.object({
  mode: z.enum(["read", "roles", "security"]),
  reason: z
    .string()
    .trim()
    .min(10, "Укажите причину подробнее, минимум 10 символов")
    .max(500, "Не более 500 символов"),
  duration_minutes: z.coerce.number().int().min(5).max(20),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  tenantId: string;
  tenantName: string;
  onClose: () => void;
  onPendingChange?: (pending: boolean) => void;
}

const ROLE_CAPABILITIES = [
  "users.view",
  "branches.view",
  "roles.create",
  "roles.update",
  "roles.assign",
];
const SECURITY_CAPABILITIES = ["users.view", "users.block"];

export function SupportAccessForm({
  tenantId,
  tenantName,
  onClose,
  onPendingChange,
}: Props): JSX.Element {
  const navigate = useNavigate();
  const capabilities = useSupportCapabilities();
  const startSession = useStartSupportSession();
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: { mode: "read", reason: "", duration_minutes: 15 },
  });

  const onSubmit = form.handleSubmit(async (values) => {
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

    const available = new Set(capabilities.data?.map((capability) => capability.code) ?? []);
    const requested = (
      parsed.data.mode === "roles"
        ? ROLE_CAPABILITIES
        : parsed.data.mode === "security"
          ? SECURITY_CAPABILITIES
          : ["users.view"]
    ).filter((code) => available.has(code));
    if (requested.length === 0) {
      setTopError("Для выбранного режима нет доступных действий.");
      return;
    }

    setTopError(null);
    onPendingChange?.(true);
    try {
      const session = await startSession.mutateAsync({
        tenant_id: tenantId,
        reason: parsed.data.reason,
        duration_minutes: parsed.data.duration_minutes,
        capabilities: requested,
      });
      await activateSupportContext(session);
      onClose();
      await navigate({ to: parsed.data.mode === "roles" ? "/roles" : "/users" });
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось открыть защищённый доступ"));
    } finally {
      onPendingChange?.(false);
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <p className="text-sm text-foreground-secondary">Аптека: {tenantName}</p>

      <fieldset>
        <legend className="text-sm font-medium text-foreground">Режим</legend>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="rounded-md border border-border p-3 text-sm">
            <input type="radio" value="read" {...form.register("mode")} />
            <span className="ml-2 font-medium">Только просмотр</span>
          </label>
          <label className="rounded-md border border-border p-3 text-sm">
            <input type="radio" value="roles" {...form.register("mode")} />
            <span className="ml-2 font-medium">Роли и назначения</span>
          </label>
          <label className="rounded-md border border-border p-3 text-sm">
            <input type="radio" value="security" {...form.register("mode")} />
            <span className="ml-2 font-medium">Безопасность</span>
          </label>
        </div>
      </fieldset>

      <div>
        <Label htmlFor="support-access-reason">Причина доступа</Label>
        <Textarea
          id="support-access-reason"
          rows={3}
          invalid={Boolean(form.formState.errors.reason)}
          placeholder="Например: настройка ролей перед запуском аптеки"
          {...form.register("reason")}
        />
        <FormError>{form.formState.errors.reason?.message}</FormError>
      </div>

      <div>
        <Label htmlFor="support-access-duration">Срок</Label>
        <Select id="support-access-duration" {...form.register("duration_minutes")}>
          <option value={5}>5 минут</option>
          <option value={10}>10 минут</option>
          <option value={15}>15 минут</option>
          <option value={20}>20 минут</option>
        </Select>
      </div>

      {capabilities.error && (
        <p className="text-sm text-danger">
          {describeApiError(capabilities.error, "Не удалось загрузить доступные действия")}
        </p>
      )}
      {topError && <p className="text-sm text-danger">{topError}</p>}

      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={form.formState.isSubmitting}
          onClick={onClose}
        >
          Отмена
        </Button>
        <Button
          type="submit"
          isLoading={form.formState.isSubmitting}
          disabled={capabilities.isLoading || capabilities.isError}
        >
          Открыть доступ
        </Button>
      </div>
    </form>
  );
}
