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
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <Field
              label="Действие"
              value={
                <Badge tone={actionTone(entry.action)}>
                  {actionLabel[entry.action] ?? entry.action}
                </Badge>
              }
            />
            <Field label="Таблица" value={tableLabel[entry.table_name] ?? entry.table_name} mono />
            <Field label="Когда" value={new Date(entry.created_at).toLocaleString("ru-RU")} />
            <Field
              label="Запись"
              value={entry.record_id ? entry.record_id.slice(0, 8) : "—"}
              mono
            />
            {entry.user_id && <Field label="Пользователь" value={entry.user_id.slice(0, 8)} mono />}
            {entry.ip_address && <Field label="IP" value={entry.ip_address} mono />}
          </div>

          <DiffTable entry={entry} />

          {entry.metadata && Object.keys(entry.metadata).length > 0 && (
            <details className="rounded-md border border-border bg-foreground/[0.03] px-3 py-2 text-sm">
              <summary className="cursor-pointer font-medium">Доп. данные</summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs">
                {JSON.stringify(entry.metadata, null, 2)}
              </pre>
            </details>
          )}

          <div className="flex justify-end">
            <Button variant="ghost" onClick={onClose}>
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
    return <p className="text-sm italic text-foreground-muted">Поля не записаны</p>;
  }

  const isUpdate = entry.action === "update";
  const changedSet = new Set<string>(
    entry.changed_fields ? Object.keys(entry.changed_fields) : keys,
  );

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="min-w-[36rem] w-full text-sm">
        <thead className="bg-foreground/[0.03] text-left">
          <tr>
            <th className="px-3 py-2 text-xs font-medium text-foreground-secondary">Поле</th>
            <th className="px-3 py-2 text-xs font-medium text-foreground-secondary">
              {isUpdate ? "Было" : entry.action === "delete" ? "Значение" : ""}
            </th>
            <th className="px-3 py-2 text-xs font-medium text-foreground-secondary">
              {entry.action === "delete" ? "" : "Стало"}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {keys.map((k) => {
            const oldV = entry.old_values?.[k];
            const newV = entry.new_values?.[k];
            const changed = isUpdate ? changedSet.has(k) : true;
            return (
              <tr key={k} className={changed ? "bg-warning-subtle/50" : ""}>
                <td className="px-3 py-2 font-mono text-xs text-foreground-secondary">{k}</td>
                <td className="px-3 py-2 font-mono text-xs text-danger">
                  {entry.old_values ? format(oldV) : ""}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-success-foreground">
                  {entry.new_values ? format(newV) : ""}
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
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={mono ? "font-mono text-xs" : ""}>{value}</p>
    </div>
  );
}
