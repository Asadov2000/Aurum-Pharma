import { type ReactNode, useState } from "react";

import {
  Badge,
  Button,
  ConfirmDialog,
  Modal,
  Pagination,
  SkeletonRows,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import {
  formatSupplierDateTime,
  formatSupplierMoney,
  formatSupplierQuantity,
  supplierProductSubtitle,
} from "./formatters";
import { supplierReturnReasonLabel } from "./labels";
import { useSupplierReturnsQuery } from "./queries";
import { SupplierReturnForm } from "./SupplierReturnForm";
import { type Supplier, type SupplierReturnDetails } from "./types";

const RETURN_PAGE_SIZE = 10;

export function SupplierDetailModal({
  supplier,
  onClose,
  onEdit,
}: {
  supplier: Supplier;
  onClose: () => void;
  onEdit: (supplier: Supplier) => void;
}): JSX.Element {
  const { user } = useAuth();
  const canUpdate = hasPermission(user, "suppliers.update");
  const canViewReturns = hasPermission(user, "incoming.view");
  const canCreateReturn = hasPermission(user, "incoming.return") && supplier.is_active;
  const [returnPage, setReturnPage] = useState(1);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnDirty, setReturnDirty] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const returns = useSupplierReturnsQuery(
    { supplier_id: supplier.id, page: returnPage, page_size: RETURN_PAGE_SIZE },
    canViewReturns,
  );
  const closeReturn = () => {
    setReturnOpen(false);
    setReturnDirty(false);
    setDiscardOpen(false);
  };
  const requestReturnClose = () => {
    if (returnDirty) setDiscardOpen(true);
    else closeReturn();
  };

  return (
    <div className="min-w-0">
      <header className="border-b border-border px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="min-w-0 break-words text-xl font-semibold text-foreground">
                {supplier.name}
              </h2>
              <Badge tone={supplier.is_active ? "success" : "neutral"}>
                {supplier.is_active ? "Доступен для приходов" : "Отключён для новых документов"}
              </Badge>
            </div>
            {supplier.legal_name && (
              <p className="mt-1 text-sm text-foreground-muted">{supplier.legal_name}</p>
            )}
            <p className="mt-2 text-sm text-foreground-secondary">
              {supplier.contact_person || "Контактное лицо не указано"}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {canUpdate && (
              <Button variant="secondary" onClick={() => onEdit(supplier)}>
                Изменить
              </Button>
            )}
            {canCreateReturn && (
              <Button onClick={() => setReturnOpen(true)}>Оформить возврат</Button>
            )}
          </div>
        </div>
      </header>

      <section
        aria-label="Контакты поставщика"
        className="grid grid-cols-1 border-b border-border bg-background/60 sm:grid-cols-2 lg:grid-cols-4"
      >
        <Metric
          label="Телефон"
          value={
            supplier.phone ? (
              <a className="hover:text-primary hover:underline" href={`tel:${supplier.phone}`}>
                {supplier.phone}
              </a>
            ) : (
              "Не указан"
            )
          }
        />
        <Metric
          label="Email"
          value={
            supplier.email ? (
              <a className="hover:text-primary hover:underline" href={`mailto:${supplier.email}`}>
                {supplier.email}
              </a>
            ) : (
              "Не указан"
            )
          }
        />
        <Metric label="ИНН поставщика" value={supplier.inn_or_tin || "Не указан"} mono />
        <Metric label="Адрес" value={supplier.address || "Не указан"} />
      </section>

      <div className="space-y-5 px-4 py-4 sm:px-5">
        {(supplier.notes || !supplier.is_active) && (
          <section aria-labelledby="supplier-notes-heading">
            <h3 id="supplier-notes-heading" className="text-sm font-semibold text-foreground">
              Рабочие заметки
            </h3>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground-secondary">
              {supplier.notes || "Поставщик отключён и недоступен для новых приходов."}
            </p>
          </section>
        )}

        {canViewReturns && (
          <section aria-labelledby="supplier-returns-heading">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 id="supplier-returns-heading" className="text-sm font-semibold text-foreground">
                  Возвраты поставщику
                </h3>
                {returns.data && (
                  <p className="mt-1 text-xs text-foreground-muted">
                    {returns.data.total} операций ·{" "}
                    {formatSupplierQuantity(returns.data.summary.total_qty)} ед. ·{" "}
                    {formatSupplierMoney(returns.data.summary.total_amount)}
                  </p>
                )}
              </div>
              {returns.isFetching && !returns.isLoading && (
                <span className="text-xs text-foreground-muted" role="status">
                  Обновление…
                </span>
              )}
            </div>

            {returns.isLoading ? (
              <div className="mt-3">
                <SkeletonRows rows={4} />
              </div>
            ) : returns.error ? (
              <div
                role="alert"
                className="mt-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
              >
                <p>{describeApiError(returns.error, "Не удалось загрузить возвраты")}</p>
                <Button
                  variant="secondary"
                  size="sm"
                  className="mt-3"
                  onClick={() => void returns.refetch()}
                >
                  Повторить
                </Button>
              </div>
            ) : !returns.data?.items.length ? (
              <p className="mt-3 rounded-lg border border-dashed border-border px-4 py-5 text-sm text-foreground-muted">
                Возвратов этому поставщику пока не оформляли.
              </p>
            ) : (
              <>
                <div className="mt-3 hidden md:block">
                  <ReturnTable items={returns.data.items} />
                </div>
                <div className="mt-3 divide-y divide-border rounded-lg border border-border md:hidden">
                  {returns.data.items.map((item) => (
                    <ReturnCard key={item.id} item={item} />
                  ))}
                </div>
                <Pagination
                  page={returnPage}
                  pageSize={RETURN_PAGE_SIZE}
                  total={returns.data.total}
                  onPage={setReturnPage}
                />
              </>
            )}
          </section>
        )}

        <div className="flex justify-end border-t border-border pt-4">
          <Button variant="ghost" className="min-h-11" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </div>

      <Modal
        open={returnOpen}
        onClose={requestReturnClose}
        title={`Возврат: ${supplier.name}`}
        className="max-w-3xl"
      >
        <SupplierReturnForm
          supplier={supplier}
          onClose={requestReturnClose}
          onDirtyChange={setReturnDirty}
        />
      </Modal>
      <ConfirmDialog
        open={discardOpen}
        title="Закрыть без сохранения?"
        message="Введённые данные возврата не сохранятся."
        cancelLabel="Продолжить"
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onCancel={() => setDiscardOpen(false)}
        onConfirm={closeReturn}
      />
    </div>
  );
}

function ReturnTable({ items }: { items: SupplierReturnDetails[] }): JSX.Element {
  return (
    <Table>
      <THead>
        <TR>
          <TH>Дата</TH>
          <TH>Товар</TH>
          <TH>Причина</TH>
          <TH>Точка</TH>
          <TH className="text-right">Количество</TH>
          <TH className="text-right">Сумма</TH>
        </TR>
      </THead>
      <TBody>
        {items.map((item) => (
          <TR key={item.id}>
            <TD className="whitespace-nowrap text-xs">
              {formatSupplierDateTime(item.created_at, item.report_timezone)}
            </TD>
            <TD>
              <p className="font-medium">{item.catalog_name}</p>
              <p className="max-w-52 truncate text-xs text-foreground-muted">
                {supplierProductSubtitle(item) || "Без характеристик"} · партия{" "}
                {item.batch_number ?? "без номера"}
              </p>
            </TD>
            <TD>{supplierReturnReasonLabel[item.reason]}</TD>
            <TD>{item.branch_name}</TD>
            <TD className="text-right font-mono tabular-nums">
              {formatSupplierQuantity(item.qty)}
            </TD>
            <TD className="whitespace-nowrap text-right font-mono tabular-nums">
              {formatSupplierMoney(item.amount, item.currency)}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function ReturnCard({ item }: { item: SupplierReturnDetails }): JSX.Element {
  return (
    <article className="px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">{item.catalog_name}</p>
          <p className="mt-0.5 text-xs text-foreground-muted">
            {formatSupplierDateTime(item.created_at, item.report_timezone)} · {item.branch_name}
          </p>
        </div>
        <strong className="shrink-0 font-mono text-sm tabular-nums">
          {formatSupplierMoney(item.amount, item.currency)}
        </strong>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-foreground-muted">{supplierReturnReasonLabel[item.reason]}</span>
        <span className="font-mono tabular-nums">{formatSupplierQuantity(item.qty)} ед.</span>
      </div>
    </article>
  );
}

function Metric({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0 border-b border-r border-border px-4 py-3 last:border-r-0 lg:border-b-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <div
        className={`mt-1 break-words text-sm font-medium ${mono ? "font-mono tabular-nums" : ""}`}
      >
        {value}
      </div>
    </div>
  );
}
