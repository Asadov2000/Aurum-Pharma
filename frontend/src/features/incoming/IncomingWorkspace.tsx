import { useState } from "react";

import { Badge, Button } from "@/components/ui";

import { pharmacyCalendarDate } from "./calendar";
import { statusLabel, statusTone } from "./labels";
import { type IncomingDocumentWithItems, type IncomingDraftRow, type IncomingItem } from "./types";

interface IncomingWorkspaceProps {
  isDesktopLayout: boolean;
  document: IncomingDocumentWithItems;
  drafts: IncomingDraftRow[];
  draftsPending: boolean;
  draftsError: boolean;
  canEdit: boolean;
  canFinalize: boolean;
  acceptBlocked: boolean;
  draftsBlockAccept: boolean;
  onEditDocument: () => void;
  onImport: () => void;
  onAdd: () => void;
  onEditItem: (item: IncomingItem) => void;
  onDeleteItem: (item: IncomingItem) => void;
  onContinueDraft: (row: IncomingDraftRow) => void;
  onDeleteDraft: (row: IncomingDraftRow) => void;
  onRetryDrafts: () => void;
  onAccept: () => void;
  onReject: () => void;
}

interface WorkspaceRow {
  key: string;
  name: string;
  details: string;
  batch: string | null;
  expiry: string;
  quantity: string;
  purchase: string | null;
  sale: string;
  currency: string;
  pending: boolean;
  issues: string[];
  onOpen?: () => void;
  onDelete?: () => void;
}

export function IncomingWorkspace(props: IncomingWorkspaceProps): JSX.Element {
  const { document: doc } = props;
  const [attentionOnly, setAttentionOnly] = useState(false);
  const isDraft = doc.status === "draft";
  const editable = isDraft && props.canEdit && !props.acceptBlocked;
  const today = pharmacyCalendarDate();
  const rows: WorkspaceRow[] = [
    ...(isDraft ? props.drafts : []).map(
      (draft): WorkspaceRow => ({
        key: `draft-${draft.id}`,
        name: draft.catalog_name || draft.fields.source_name || "Товар ещё не выбран",
        details:
          draft.catalog_name && draft.fields.source_name
            ? `В накладной: ${draft.fields.source_name}`
            : "Незавершённая позиция · не входит в сумму документа",
        batch: draft.fields.batch_number,
        expiry: draft.fields.expires_at,
        quantity: draft.fields.qty,
        purchase: draft.fields.purchase_price,
        sale: draft.fields.sale_price,
        currency: doc.currency,
        pending: true,
        issues: [draft.fields.catalog_id ? "Завершите строку" : "Выберите товар"],
        onOpen: editable ? () => props.onContinueDraft(draft) : undefined,
        onDelete: editable ? () => props.onDeleteDraft(draft) : undefined,
      }),
    ),
    ...doc.items.map(
      (item): WorkspaceRow => ({
        key: `item-${item.id}`,
        name: item.catalog_name?.trim() || `Товар ${item.catalog_id.slice(0, 8)}`,
        details: [item.catalog_form, item.catalog_dosage, item.catalog_pack_size]
          .filter(Boolean)
          .join(" · "),
        batch: item.batch_number,
        expiry: item.expires_at,
        quantity: item.qty,
        purchase: item.purchase_price,
        sale: item.sale_price,
        currency: item.currency,
        pending: false,
        issues: [
          ...(item.expires_at <= today ? ["Срок истёк"] : []),
          ...(item.purchase_price !== null && Number(item.sale_price) < Number(item.purchase_price)
            ? ["Продажа ниже закупки"]
            : []),
        ],
        onOpen: editable ? () => props.onEditItem(item) : undefined,
        onDelete: editable ? () => props.onDeleteItem(item) : undefined,
      }),
    ),
  ];
  const attentionCount = rows.filter((row) => row.issues.length > 0).length;
  const visibleRows = attentionOnly ? rows.filter((row) => row.issues.length > 0) : rows;
  const acceptDisabled = doc.items.length === 0 || props.draftsBlockAccept;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="break-words text-2xl font-semibold tracking-tight">
              {doc.document_number ? `Приёмка № ${doc.document_number}` : "Приёмка без номера"}
            </h1>
            <Badge tone={statusTone[doc.status]}>{statusLabel[doc.status]}</Badge>
          </div>
          <p className="text-sm text-foreground-muted">
            {isDraft
              ? "Проверьте товары, количество, цены и сроки годности."
              : "Документ закрыт для изменений."}
          </p>
        </div>
        {editable && (
          <Button variant="secondary" onClick={props.onEditDocument}>
            Изменить реквизиты
          </Button>
        )}
      </header>

      <section
        aria-label="Реквизиты приёмки"
        className="rounded-xl border border-border bg-surface p-4 sm:p-5"
      >
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Detail
            label="Поставщик"
            value={doc.supplier_name ?? `Поставщик ${doc.supplier_id.slice(0, 8)}`}
          />
          <Detail
            label="Аптечная точка"
            value={doc.branch_name ?? `Точка ${doc.branch_id.slice(0, 8)}`}
          />
          <Detail label="Дата документа" value={formatDate(doc.document_date)} />
          <Detail label="Номер документа поставщика" value={doc.document_number || "Не указан"} />
        </dl>
        {doc.notes && (
          <div className="mt-4 border-t border-border pt-3">
            <p className="text-xs text-foreground-muted">Комментарий</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm">{doc.notes}</p>
          </div>
        )}
      </section>

      <section aria-labelledby="workspace-items-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="workspace-items-heading" className="text-lg font-semibold">
            Товары в накладной
          </h2>
          {editable && (
            <div className="flex w-full flex-wrap gap-2 sm:w-auto">
              <Button className="min-h-11" variant="secondary" onClick={props.onImport}>
                Загрузить накладную
              </Button>
              <Button className="min-h-11" onClick={props.onAdd}>
                Добавить позицию
              </Button>
            </div>
          )}
        </div>
        <div aria-label="Фильтр позиций" role="group" className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={attentionOnly ? "ghost" : "secondary"}
            aria-pressed={!attentionOnly}
            onClick={() => setAttentionOnly(false)}
          >
            Все · {rows.length}
          </Button>
          <Button
            size="sm"
            variant={attentionOnly ? "secondary" : "ghost"}
            aria-pressed={attentionOnly}
            onClick={() => setAttentionOnly(true)}
          >
            Требуют внимания · {attentionCount}
          </Button>
        </div>
        {isDraft && props.draftsError && (
          <div
            role="alert"
            className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-sm"
          >
            <p>Не удалось проверить черновые позиции. Принятие прихода временно недоступно.</p>
            <Button variant="secondary" size="sm" className="mt-2" onClick={props.onRetryDrafts}>
              Повторить проверку
            </Button>
          </div>
        )}
        {isDraft && props.draftsPending && (
          <p role="status" className="text-sm text-foreground-muted">
            Проверяем незавершённые позиции…
          </p>
        )}
        {visibleRows.length === 0 ? (
          <div className="rounded-xl border border-border bg-surface px-4 py-10 text-center">
            <p className="font-medium">
              {attentionOnly ? "Нет позиций, требующих внимания" : "Документ пока пуст"}
            </p>
            <p className="mt-2 text-sm text-foreground-muted">
              {attentionOnly
                ? "Все позиции доступны на вкладке «Все»."
                : "Здесь появятся товары накладной."}
            </p>
          </div>
        ) : props.isDesktopLayout ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Позиции приёмки</caption>
              <thead className="border-b border-border bg-surface-subtle text-xs text-foreground-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Товар
                  </th>
                  <th scope="col" className="px-3 py-3 font-medium">
                    Партия / срок
                  </th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">
                    Количество
                  </th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">
                    Закупка / ед.
                  </th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">
                    Продажа / ед.
                  </th>
                  {editable && (
                    <th scope="col" className="px-3 py-3 font-medium">
                      Действия
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {visibleRows.map((row) => (
                  <tr key={row.key} className={row.pending ? "bg-warning-subtle/30" : undefined}>
                    <td className="min-w-52 max-w-sm px-4 py-4">
                      <RowIdentity row={row} />
                    </td>
                    <td className="px-3 py-4">
                      <p className="break-words">{row.batch || "Без номера"}</p>
                      <p className="mt-1 whitespace-nowrap text-xs text-foreground-muted">
                        до {formatDate(row.expiry)}
                      </p>
                    </td>
                    <td className="px-3 py-4 text-right tabular-nums">
                      {formatNumber(row.quantity)}
                    </td>
                    <td className="px-3 py-4 text-right tabular-nums">
                      {formatMoney(row.purchase, row.currency)}
                    </td>
                    <td className="px-3 py-4 text-right tabular-nums">
                      {formatMoney(row.sale, row.currency)}
                    </td>
                    {editable && (
                      <td className="px-3 py-4">
                        <RowActions row={row} />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
            {visibleRows.map((row) => (
              <article key={row.key} aria-label={row.name} className="space-y-4 p-4">
                <RowIdentity row={row} />
                <dl className="grid grid-cols-2 gap-3">
                  <Detail label="Номер партии" value={row.batch || "Без номера"} />
                  <Detail label="Срок годности" value={formatDate(row.expiry)} />
                  <Detail label="Количество" value={formatNumber(row.quantity)} />
                  <Detail label="Закупка / ед." value={formatMoney(row.purchase, row.currency)} />
                  <Detail label="Продажа / ед." value={formatMoney(row.sale, row.currency)} />
                </dl>
                {editable && <RowActions row={row} />}
              </article>
            ))}
          </div>
        )}
        {isDraft && (
          <p className="text-xs text-foreground-muted">
            Незавершённые позиции не входят в сумму. Товары появятся на складе после принятия
            прихода.
          </p>
        )}
      </section>

      <footer className="space-y-4 rounded-xl border border-border bg-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-foreground-muted">Позиций в документе: {doc.items.length}</p>
          <div className="text-right">
            <p className="text-xs text-foreground-muted">Закупочная сумма</p>
            <p className="mt-1 text-xl font-semibold tabular-nums">
              {formatMoney(doc.total_amount, doc.currency)}
            </p>
          </div>
        </div>
        {isDraft && props.canFinalize && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <p id="workspace-accept-help" className="text-sm text-foreground-muted">
              {props.draftsBlockAccept
                ? "Сначала проверьте и завершите незавершённые позиции."
                : doc.items.length === 0
                  ? "Сначала добавьте хотя бы одну позицию."
                  : "Перед принятием сверьте данные с накладной и упаковками."}
            </p>
            <div className="flex w-full flex-wrap gap-2 sm:w-auto">
              <Button variant="secondary" disabled={props.acceptBlocked} onClick={props.onReject}>
                Отклонить
              </Button>
              <Button
                disabled={acceptDisabled}
                aria-describedby="workspace-accept-help"
                onClick={props.onAccept}
              >
                Принять на склад
              </Button>
            </div>
          </div>
        )}
      </footer>
    </div>
  );
}

function RowIdentity({ row }: { row: WorkspaceRow }): JSX.Element {
  return (
    <div className="space-y-1.5">
      {row.onOpen ? (
        <button
          type="button"
          onClick={row.onOpen}
          className="min-h-11 break-words text-left font-medium text-primary underline-offset-4 hover:underline"
        >
          {row.name}
        </button>
      ) : (
        <p className="break-words font-medium">{row.name}</p>
      )}
      {row.details && <p className="break-words text-xs text-foreground-muted">{row.details}</p>}
      {row.issues.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {row.issues.map((issue) => (
            <Badge key={issue} tone="warning">
              {issue}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

function RowActions({ row }: { row: WorkspaceRow }): JSX.Element {
  return (
    <div className="flex flex-wrap gap-1">
      {row.onOpen && (
        <Button size="sm" variant="secondary" className="min-h-11" onClick={row.onOpen}>
          {row.pending ? "Продолжить" : "Изменить"}
        </Button>
      )}
      {row.onDelete && (
        <Button size="sm" variant="ghost" className="min-h-11" onClick={row.onDelete}>
          Удалить
        </Button>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-medium">{value}</dd>
    </div>
  );
}

const quantityFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const dateFormatter = new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC" });

function decimal(value: string): number | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value: string): string {
  if (!value.trim()) return "Не указано";
  const parsed = decimal(value);
  return parsed === null ? "Проверьте значение" : quantityFormatter.format(parsed);
}

function formatMoney(value: string | null, currency: string): string {
  if (value === null) return "Доступ ограничен";
  if (!value.trim()) return "Не указано";
  const parsed = decimal(value);
  return parsed === null ? "Проверьте значение" : `${moneyFormatter.format(parsed)} ${currency}`;
}

function formatDate(value: string): string {
  if (!value) return "Не указан";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Проверьте дату";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value) {
    return "Проверьте дату";
  }
  return dateFormatter.format(date);
}
