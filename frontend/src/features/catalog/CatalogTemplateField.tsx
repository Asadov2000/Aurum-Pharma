import { useState, type InputHTMLAttributes } from "react";
import { isAxiosError } from "axios";

import { Button, ConfirmDialog, Input, Label } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { permissionBranchScope } from "@/features/auth/branchPermissions";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import {
  useCreateFieldTemplate,
  useDeleteFieldTemplate,
  useFieldTemplates,
  type CatalogFieldTemplateKind,
} from "./fieldTemplates";

interface Props {
  kind: CatalogFieldTemplateKind;
  label: string;
  placeholder?: string;
  value: string;
  inputProps: InputHTMLAttributes<HTMLInputElement>;
}

export function CatalogTemplateField({
  kind,
  label,
  placeholder,
  value,
  inputProps,
}: Props): JSX.Element {
  const { user } = useAuth();
  const canView =
    hasPermission(user, "catalog.view") && permissionBranchScope(user, "catalog.view") === null;
  const canManage =
    hasPermission(user, "catalog.update") && permissionBranchScope(user, "catalog.update") === null;
  const templates = useFieldTemplates(user?.active_tenant_id, canView);
  const create = useCreateFieldTemplate();
  const remove = useDeleteFieldTemplate();
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const options = templates.data?.filter((template) => template.kind === kind) ?? [];
  const trimmed = value.trim().replace(/\s+/g, " ");
  const matching = options.find(
    (template) => template.value.toLocaleLowerCase("ru-RU") === trimmed.toLocaleLowerCase("ru-RU"),
  );
  const busy = create.isPending || remove.isPending;

  async function saveTemplate() {
    if (!trimmed || trimmed.length > 120 || matching) return;
    setError(null);
    try {
      await create.mutateAsync({ kind, value: trimmed });
    } catch (cause) {
      setError(
        isAxiosError(cause) && cause.response?.status === 409
          ? "Такой шаблон уже есть. Обновите список и выберите его."
          : describeApiError(cause, "Не удалось сохранить шаблон"),
      );
    }
  }

  async function removeTemplate() {
    if (!matching) return;
    setError(null);
    try {
      await remove.mutateAsync(matching.id);
    } catch (cause) {
      setError(describeApiError(cause, "Не удалось удалить шаблон"));
    } finally {
      setConfirmOpen(false);
    }
  }

  return (
    <div>
      <Label htmlFor={kind}>{label}</Label>
      <Input id={kind} list={`${kind}-templates`} placeholder={placeholder} {...inputProps} />
      <datalist id={`${kind}-templates`}>
        {options.map((template) => (
          <option key={template.id} value={template.value} />
        ))}
      </datalist>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {canView && canManage && matching ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmOpen(true)}>
            Удалить шаблон
          </Button>
        ) : canView && canManage && trimmed && trimmed.length <= 120 ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || templates.isLoading || templates.isError}
            isLoading={create.isPending}
            onClick={() => void saveTemplate()}
          >
            Сохранить как шаблон
          </Button>
        ) : null}
        {templates.isError && (
          <span className="text-xs text-foreground-muted">Шаблоны временно недоступны</span>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
      <ConfirmDialog
        open={confirmOpen && Boolean(matching)}
        title="Удалить шаблон?"
        message={`«${matching?.value ?? ""}» исчезнет из подсказок, но останется в уже созданных карточках.`}
        confirmLabel="Удалить шаблон"
        variant="danger"
        isLoading={remove.isPending}
        cancelDisabled={remove.isPending}
        onConfirm={() => void removeTemplate()}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}
