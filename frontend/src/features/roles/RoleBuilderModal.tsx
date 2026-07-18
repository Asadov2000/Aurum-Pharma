import { useMemo, useState } from "react";

import { Badge, Button, Input, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { groupLabel } from "./labels";
import { useCreateRole, usePermissionsQuery, useTemplatesQuery, useUpdateRole } from "./queries";
import { hasUnavailableRolePermissions, ROLE_EDIT_BLOCKED_MESSAGE } from "./roleAccess";
import { type Permission, type Role } from "./types";

interface Props {
  mode: "create" | "edit";
  role?: Role;
  onClose: () => void;
}

/** Group permissions by group_code, preserving the API's order (it returns them
 *  sorted by group_code, code), keeping only the ones passed in. */
function groupBy(perms: Permission[]): { code: string; items: Permission[] }[] {
  const order: string[] = [];
  const byGroup = new Map<string, Permission[]>();
  for (const p of perms) {
    if (!byGroup.has(p.group_code)) {
      byGroup.set(p.group_code, []);
      order.push(p.group_code);
    }
    byGroup.get(p.group_code)!.push(p);
  }
  return order.map((code) => ({ code, items: byGroup.get(code) ?? [] }));
}

export function RoleBuilderModal({ mode, role, onClose }: Props): JSX.Element {
  const permsQuery = usePermissionsQuery();
  const templatesQuery = useTemplatesQuery(mode === "create");
  const createRole = useCreateRole();
  const updateRole = useUpdateRole();

  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [templateId, setTemplateId] = useState("");
  const [checked, setChecked] = useState<Set<string>>(() => new Set(role?.permissions ?? []));
  const [nameError, setNameError] = useState<string | null>(null);
  const [topError, setTopError] = useState<string | null>(null);

  // The server returns the grantable catalogue for this actor. The client must
  // neither expand it for support accounts nor infer delegation from JWT data.
  const visible = useMemo(() => permsQuery.data ?? [], [permsQuery.data]);
  const visibleCodes = useMemo(() => new Set(visible.map((p) => p.code)), [visible]);
  const groups = useMemo(() => groupBy(visible), [visible]);
  const editBlocked =
    mode === "edit" &&
    role !== undefined &&
    permsQuery.isSuccess &&
    hasUnavailableRolePermissions(role);

  const toggle = (code: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });

  const onTemplateChange = (id: string) => {
    setTemplateId(id);
    const tpl = templatesQuery.data?.find((t) => t.id === id);
    if (!tpl) {
      setChecked(new Set());
      return;
    }
    // Templates are hints only and may tick exclusively grantable catalogue items.
    setChecked(new Set(tpl.permissions.filter((c) => visibleCodes.has(c))));
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editBlocked) {
      setTopError(ROLE_EDIT_BLOCKED_MESSAGE);
      return;
    }
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError("Введите название роли");
      return;
    }
    setNameError(null);
    setTopError(null);
    const codes = [...checked].filter((code) => visibleCodes.has(code));
    try {
      if (mode === "edit" && role) {
        await updateRole.mutateAsync({
          id: role.id,
          payload: {
            expected_version: role.version,
            name: trimmed,
            description: description.trim() || null,
            permissions: codes,
          },
        });
      } else {
        await createRole.mutateAsync({
          name: trimmed,
          description: description.trim() || null,
          permissions: codes,
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить роль"));
    }
  };

  const submitting = createRole.isPending || updateRole.isPending;

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="role-name">Название</Label>
        <Input
          id="role-name"
          value={name}
          disabled={editBlocked}
          invalid={Boolean(nameError)}
          onChange={(e) => setName(e.target.value)}
          placeholder="Например: Старший кассир"
        />
        {nameError && <p className="mt-1 text-sm text-danger">{nameError}</p>}
      </div>

      <div>
        <Label htmlFor="role-desc">Описание (необязательно)</Label>
        <Textarea
          id="role-desc"
          value={description}
          disabled={editBlocked}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Коротко, для чего эта роль"
        />
      </div>

      {mode === "create" && (
        <div>
          <Label htmlFor="role-template">Начать из шаблона (необязательно)</Label>
          <Select
            id="role-template"
            value={templateId}
            disabled={templatesQuery.isLoading || templatesQuery.isError}
            onChange={(e) => onTemplateChange(e.target.value)}
          >
            <option value="">— с нуля —</option>
            {templatesQuery.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-foreground-muted">
            Шаблон только расставит галочки — дальше их можно править.
          </p>
          {templatesQuery.isLoading && (
            <p className="mt-1 text-xs text-foreground-muted">Загрузка шаблонов…</p>
          )}
          {templatesQuery.error && (
            <p className="mt-1 text-xs text-danger">
              {describeApiError(templatesQuery.error, "Не удалось загрузить шаблоны")}
            </p>
          )}
        </div>
      )}

      <div>
        <Label>Функции роли</Label>
        {editBlocked && (
          <p className="mb-2 text-sm text-danger" role="alert">
            {ROLE_EDIT_BLOCKED_MESSAGE}
          </p>
        )}
        {permsQuery.isLoading ? (
          <p className="text-sm text-foreground-muted">Загрузка…</p>
        ) : permsQuery.error ? (
          <p className="text-sm text-danger">
            {describeApiError(permsQuery.error, "Не удалось загрузить доступные функции")}
          </p>
        ) : groups.length === 0 ? (
          <p className="text-sm text-foreground-muted">Нет доступных функций.</p>
        ) : (
          <div className="mt-1 max-h-[42vh] space-y-4 overflow-y-auto rounded-md border border-border bg-foreground/[0.02] p-3">
            {groups.map((g) => (
              <fieldset key={g.code}>
                <legend className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                  {groupLabel(g.code)}
                </legend>
                <div className="mt-1 space-y-1.5">
                  {g.items.map((p) => (
                    <label
                      key={p.code}
                      title={p.description ?? undefined}
                      className="flex cursor-pointer items-start gap-2"
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 rounded border-input accent-primary"
                        checked={checked.has(p.code)}
                        disabled={editBlocked}
                        onChange={() => toggle(p.code)}
                      />
                      <span className="leading-tight">
                        <span className="flex flex-wrap items-center gap-2 text-sm text-foreground">
                          {p.name}
                          {p.is_dangerous && <Badge tone="danger">опасное право</Badge>}
                        </span>
                        {p.description && (
                          <span className="block text-xs text-foreground-muted">
                            {p.description}
                          </span>
                        )}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>
        )}
      </div>

      {topError && <p className="text-sm text-danger">{topError}</p>}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button
          type="submit"
          isLoading={submitting}
          disabled={permsQuery.isLoading || permsQuery.isError || editBlocked}
        >
          {mode === "edit" ? "Сохранить" : "Создать роль"}
        </Button>
      </div>
    </form>
  );
}
