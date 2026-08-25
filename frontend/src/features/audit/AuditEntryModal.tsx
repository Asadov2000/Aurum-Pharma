import { Badge, Button, Modal } from "@/components/ui";

import { actionLabel, actionTone, tableLabel } from "./labels";
import { type AuditEntry } from "./types";

// Build a unified key list across old/new so we can render one row per field.
function diffKeys(entry: AuditEntry): string[] {
  const keys = new Set<string>();
  if (entry.old_values) Object.keys(entry.old_values).forEach((k) => keys.add(k));
  if (entry.new_values) Object.keys(entry.new_values).forEach((k) => keys.add(k));
  return Array.from(keys).sort();
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function AuditEntryModal({
  entry,
  onClose,
}: {
  entry: AuditEntry | null;
  onClose: () => void;
}): JSX.Element {
  return (
    <Modal
      open={entry !== null}
      onClose={onClose}
      title={
        entry
          ? `${tableLabel[entry.table_name] ?? entry.table_name} · ${actionLabel[entry.action] ?? entry.action}`
          : "Запись"
      }
      className="max-w-3xl"
    >
      {entry && (
        <div className="space-y-5">
          <section
            className="overflow-hidden rounded-lg border border-border"
            aria-label="Сведения о событии"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-background px-4 py-3">
              <Badge tone={actionTone(entry.action)}>
                {actionLabel[entry.action] ?? entry.action}
              </Badge>
              <time
                dateTime={entry.created_at}
                className="text-xs tabular-nums text-foreground-muted"
              >
                {new Date(entry.created_at).toLocaleString("ru-RU")}
              </time>
            </div>

            <div className="grid grid-cols-1 gap-x-6 gap-y-4 px-4 py-4 text-sm sm:grid-cols-2">
              <Field
                label="Раздел данных"
                value={tableLabel[entry.table_name] ?? entry.table_name}
              />
              <Field label="Системное имя" value={entry.table_name} mono />
              <Field label="ID объекта" value={entry.record_id ?? "—"} mono />
              <Field label="ID пользователя" value={entry.user_id ?? "Системное событие"} mono />
              <Field label="ID аптеки" value={entry.tenant_id ?? "Платформа"} mono />
              <Field label="IP-адрес" value={entry.ip_address ?? "—"} mono />
              {entry.user_agent ? (
                <div className="sm:col-span-2">
                  <Field label="Устройство и браузер" value={entry.user_agent} mono />
                </div>
              ) : null}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="audit-change-heading">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 id="audit-change-heading" className="text-sm font-semibold text-foreground">
                Изменения данных
              </h3>
              {entry.changed_fields ? (
                <span className="text-xs text-foreground-muted">
                  Полей изменено: {Object.keys(entry.changed_fields).length}
                </span>
              ) : null}
            </div>
            <DiffTable entry={entry} />
          </section>

          {entry.metadata && Object.keys(entry.metadata).length > 0 && (
            <details className="rounded-lg border border-border bg-background px-4 py-3 text-sm">
              <summary className="cursor-pointer font-medium text-foreground">
                Технические данные события
              </summary>
              <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-foreground/[0.035] p-3 font-mono text-xs text-foreground-secondary">
                {JSON.stringify(entry.metadata, null, 2)}
              </pre>
            </details>
          )}

          <div className="flex justify-end border-t border-border pt-4">
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function DiffTable({ entry }: { entry: AuditEntry }): JSX.Element {
  const keys = diffKeys(entry);
  if (keys.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background px-4 py-6 text-center">
        <p className="text-sm text-foreground-muted">Изменяемые поля не записаны</p>
      </div>
    );
  }

  const normalizedAction = entry.action.toLowerCase();
  const isUpdate = normalizedAction === "update";
  const isDelete = normalizedAction === "delete";
  const changedSet = new Set<string>(
    entry.changed_fields ? Object.keys(entry.changed_fields) : keys,
  );

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[36rem] text-sm">
        <thead className="bg-background text-left">
          <tr>
            <th className="px-3 py-2 text-xs font-semibold text-foreground-muted">Поле</th>
            <th className="px-3 py-2 text-xs font-semibold text-foreground-muted">
              {isUpdate ? "Было" : isDelete ? "Значение" : ""}
            </th>
            <th className="px-3 py-2 text-xs font-semibold text-foreground-muted">
              {isDelete ? "" : "Стало"}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {keys.map((key) => {
            const oldValue = entry.old_values?.[key];
            const newValue = entry.new_values?.[key];
            const changed = isUpdate ? changedSet.has(key) : true;
            return (
              <tr key={key} className={changed ? "bg-warning-subtle/50" : ""}>
                <td className="px-3 py-2 font-mono text-xs text-foreground-secondary">{key}</td>
                <td className="max-w-xs break-words px-3 py-2 font-mono text-xs text-danger">
                  {entry.old_values ? format(oldValue) : ""}
                </td>
                <td className="max-w-xs break-words px-3 py-2 font-mono text-xs text-success-foreground">
                  {entry.new_values ? format(newValue) : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-foreground-muted">{label}</p>
      <p
        className={
          mono
            ? "mt-1 break-all font-mono text-xs leading-5 text-foreground-secondary"
            : "mt-1 break-words font-medium text-foreground"
        }
      >
        {value}
      </p>
    </div>
  );
}
