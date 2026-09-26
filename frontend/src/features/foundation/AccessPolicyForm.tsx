import { isAxiosError } from "axios";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, FormError, Input, Label, Select, SkeletonRows, Textarea } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import {
  ACCESS_MODULES,
  useAccessPolicy,
  useUpdateAccessPolicy,
  type AccessModule,
  type AccessPolicy,
} from "./accessPolicy";

const moduleLabels: Record<AccessModule, string> = {
  catalog: "Каталог",
  incoming: "Приёмка",
  inventory: "Склад",
  suppliers: "Поставщики",
  pos: "Касса",
  staff: "Сотрудники",
  settings: "Настройки",
};
const quota = z
  .string()
  .trim()
  .refine(
    (value) =>
      value === "" ||
      (/^\d+$/.test(value) &&
        Number.isSafeInteger(Number(value)) &&
        Number(value) >= 1 &&
        Number(value) <= 2147483647),
    "Введите целое число от 1 до 2147483647 или оставьте поле пустым",
  );
const schema = z.object({
  mode: z.enum(["full", "readonly"]),
  blocked_modules: z.array(z.enum(ACCESS_MODULES)),
  max_branches: quota,
  max_users: quota,
  max_registers: quota,
  reason: z
    .string()
    .trim()
    .min(3, "Укажите причину: минимум 3 символа")
    .max(500, "Не более 500 символов"),
  expires_at: z
    .string()
    .refine(
      (value) =>
        value === "" || (Number.isFinite(Date.parse(value)) && Date.parse(value) > Date.now()),
      "Укажите будущую дату или оставьте поле пустым",
    ),
});
type Values = z.infer<typeof schema>;
function localDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
export function AccessPolicyForm({
  tenantId,
  canManage,
  onClose,
}: {
  tenantId: string;
  canManage: boolean;
  onClose: () => void;
}): JSX.Element {
  const query = useAccessPolicy(tenantId);
  if (query.isPending) return <SkeletonRows rows={5} />;
  if (!query.data)
    return (
      <div role="alert">
        {describeApiError(query.error, "Не удалось загрузить ограничения")}
        <Button onClick={() => void query.refetch()}>Повторить</Button>
      </div>
    );
  return (
    <PolicyEditor
      key={query.data.version}
      tenantId={tenantId}
      policy={query.data}
      canManage={canManage}
      onClose={onClose}
      onReload={() => void query.refetch()}
    />
  );
}
function PolicyEditor({
  tenantId,
  policy,
  canManage,
  onClose,
  onReload,
}: {
  tenantId: string;
  policy: AccessPolicy;
  canManage: boolean;
  onClose: () => void;
  onReload: () => void;
}): JSX.Element {
  const mutation = useUpdateAccessPolicy(tenantId);
  const [preview, setPreview] = useState<Values | null>(null);
  const form = useForm<Values>({
    defaultValues: {
      mode: policy.mode,
      blocked_modules: policy.blocked_modules,
      max_branches: policy.max_branches?.toString() ?? "",
      max_users: policy.max_users?.toString() ?? "",
      max_registers: policy.max_registers?.toString() ?? "",
      reason: "",
      expires_at:
        policy.expires_at && Date.parse(policy.expires_at) > Date.now()
          ? localDate(policy.expires_at)
          : "",
    },
  });
  const conflict = isAxiosError(mutation.error) && mutation.error.response?.status === 409;
  const onSubmit = form.handleSubmit((values) => {
    form.clearErrors();
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues)
        form.setError(issue.path[0] as keyof Values, { message: issue.message });
      return;
    }
    setPreview(parsed.data);
  });
  const save = () => {
    if (!preview || !canManage || mutation.isPending) return;
    mutation.mutate(
      {
        ...preview,
        max_branches: preview.max_branches === "" ? null : Number(preview.max_branches),
        max_users: preview.max_users === "" ? null : Number(preview.max_users),
        max_registers: preview.max_registers === "" ? null : Number(preview.max_registers),
        expires_at: preview.expires_at ? new Date(preview.expires_at).toISOString() : null,
        expected_version: policy.version,
      },
      { onSuccess: onClose },
    );
  };
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <p className="text-sm text-foreground-muted">
        Ограничения Aurum действуют поверх прав сотрудников. Чтение остаётся доступным по правам
        роли. Лимиты запрещают добавление новых объектов и не удаляют существующие.
      </p>
      {policy.reason && <p className="text-sm">Текущая причина: {policy.reason}</p>}
      {policy.expires_at && Date.parse(policy.expires_at) <= Date.now() && (
        <p role="status" className="text-sm">
          Срок политики истёк; прежний статус аптеки сохраняется.
        </p>
      )}
      <fieldset
        disabled={!canManage || mutation.isPending || preview !== null}
        className="space-y-4"
      >
        <div>
          <Label htmlFor="access-mode">Режим доступа</Label>
          <Select id="access-mode" {...form.register("mode")}>
            <option value="full">Полный доступ по правам роли</option>
            <option value="readonly">Рабочие операции только для чтения</option>
          </Select>
          <p className="mt-1 text-xs text-foreground-muted">
            Архив не снимается. Если прежний запуск не подтверждён, аптека вернётся к подготовке.
          </p>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Запретить изменения в разделах</legend>
          <div className="grid grid-cols-2 gap-2">
            {ACCESS_MODULES.map((module) => (
              <label key={module} className="flex items-center gap-2 text-sm">
                <input type="checkbox" value={module} {...form.register("blocked_modules")} />
                {moduleLabels[module]}
              </label>
            ))}
          </div>
        </fieldset>
        {(
          [
            ["max_branches", "Лимит точек"],
            ["max_users", "Лимит сотрудников"],
            ["max_registers", "Лимит рабочих мест"],
          ] as const
        ).map(([field, label]) => (
          <div key={field}>
            <Label htmlFor={field}>{label}</Label>
            <Input
              id={field}
              type="number"
              min="1"
              step="1"
              placeholder="Без ограничения"
              {...form.register(field)}
            />
            <FormError>{form.formState.errors[field]?.message}</FormError>
          </div>
        ))}
        <div>
          <Label htmlFor="access-expiry">Ограничения действуют до</Label>
          <Input id="access-expiry" type="datetime-local" {...form.register("expires_at")} />
          <p className="text-xs text-foreground-muted">
            Часовой пояс этого устройства. Пустое поле — без срока окончания.
          </p>
          <FormError>{form.formState.errors.expires_at?.message}</FormError>
        </div>
        {canManage && (
          <div>
            <Label htmlFor="access-reason">Причина изменения</Label>
            <Textarea id="access-reason" maxLength={500} {...form.register("reason")} />
            <FormError>{form.formState.errors.reason?.message}</FormError>
          </div>
        )}
      </fieldset>
      {preview && (
        <div className="space-y-2 rounded-lg border border-warning/30 p-3" role="status">
          <h3 className="font-semibold">Последствия изменения</h3>
          <p>
            {preview.mode === "readonly"
              ? "Рабочие операции доступны только для чтения."
              : "Изменения разрешены по правам сотрудников, кроме выбранных разделов."}
          </p>
          <p>
            Запрет изменений:{" "}
            {preview.blocked_modules.map((module) => moduleLabels[module]).join(", ") || "нет"}.
          </p>
          {preview.blocked_modules.includes("inventory") && (
            <p>
              Запрет изменений склада также остановит продажи, приёмку и возвраты, меняющие остатки.
            </p>
          )}
          <p>
            Лимиты: точки — {preview.max_branches || "без ограничения"}; сотрудники —{" "}
            {preview.max_users || "без ограничения"}; рабочие места —{" "}
            {preview.max_registers || "без ограничения"}.
          </p>
          <p>
            {preview.expires_at
              ? `До ${new Date(preview.expires_at).toLocaleString("ru-RU")}`
              : "Без срока окончания"}
            . Существующие данные сохраняются.
          </p>
          <p>Причина: {preview.reason}</p>
        </div>
      )}
      {mutation.error && (
        <div role="alert" className="text-sm text-danger">
          {conflict
            ? "Ограничения уже изменены другим администратором. Загрузите актуальные данные и проверьте изменения заново."
            : describeApiError(mutation.error, "Не удалось сохранить ограничения")}{" "}
          {conflict && (
            <Button type="button" onClick={onReload}>
              Загрузить актуальные данные
            </Button>
          )}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={mutation.isPending}
          onClick={
            preview
              ? () => {
                  setPreview(null);
                  mutation.reset();
                }
              : onClose
          }
        >
          {preview ? "Назад" : "Закрыть"}
        </Button>
        {canManage &&
          (preview ? (
            <Button type="button" isLoading={mutation.isPending} disabled={conflict} onClick={save}>
              Подтвердить изменение
            </Button>
          ) : (
            <Button type="submit">Проверить последствия</Button>
          ))}
      </div>
    </form>
  );
}
