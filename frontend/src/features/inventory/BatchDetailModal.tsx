import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  Modal,
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
import { cn } from "@/lib/utils";

import {
  expiryHint,
  formatInventoryDate,
  formatInventoryDateTime,
  formatInventoryMoney,
  formatInventoryQuantity,
  productSubtitle,
} from "./formatters";
import { expiryLabel, expiryTone, movementLabel, movementSourceLabel } from "./labels";
import { useBatchQuery } from "./queries";
import { type Movement } from "./types";
import { WriteOffForm } from "./WriteOffForm";

export function BatchDetailModal({
  batchId,
  onClose,
  mode = "modal",
  onOpenFull,
}: {
  batchId: string;
  onClose: () => void;
  mode?: "modal" | "preview";
  onOpenFull?: () => void;
}): JSX.Element {
  const { user } = useAuth();
  const canWriteOff = hasPermission(user, "batches.write_off");
  const batchQuery = useBatchQuery(batchId);
  const [writeOffOpen, setWriteOffOpen] = useState(false);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");
  const isPreview = mode === "preview";

  if (batchQuery.isLoading) {
    return (
      <div className="p-4 sm:p-5">
        <SkeletonRows rows={5} />
      </div>
    );
  }
  if (batchQuery.error || !batchQuery.data) {
    return (
      <div role="alert" className="p-4 text-sm text-danger sm:p-5">
        <p>{describeApiError(batchQuery.error, "Не удалось загрузить партию")}</p>
        <Button
          variant="secondary"
          size="sm"
          className="mt-3"
          isLoading={batchQuery.isFetching}
          onClick={() => void batchQuery.refetch()}
        >
          Повторить
        </Button>
      </div>
    );
  }

  const batch = batchQuery.data;
  const subtitle = productSubtitle(batch);
  const purchaseValue = Number(batch.purchase_price) * Number(batch.qty_remaining);
  const saleValue = Number(batch.sale_price) * Number(batch.qty_remaining);
  const marginValue = saleValue - purchaseValue;
  const canWriteOffCurrentBatch =
    canWriteOff && !batch.is_blocked && Number(batch.qty_remaining) > 0;

  return (
    <div className="min-w-0">
      <header className="border-b border-border px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2
                className={cn(
                  "min-w-0 break-words font-semibold text-foreground",
                  isPreview ? "text-lg" : "text-xl",
                )}
              >
                {batch.catalog_name}
              </h2>
              <Badge tone={expiryTone[batch.expiry_status]}>
                {expiryLabel[batch.expiry_status]}
              </Badge>
              {batch.is_blocked && <Badge tone="danger">Заблокирована</Badge>}
            </div>
            {subtitle && <p className="mt-1 text-sm text-foreground-muted">{subtitle}</p>}
            <p className="mt-2 text-sm text-foreground-secondary">
              {batch.branch_name} · партия {batch.batch_number ?? "без номера"}
            </p>
          </div>
          {canWriteOffCurrentBatch && (
            <Button variant="secondary" onClick={() => setWriteOffOpen(true)}>
              Списать
            </Button>
          )}
        </div>
      </header>

      <section
        aria-label="Остаток и стоимость партии"
        className={cn(
          "grid grid-cols-2 border-b border-border bg-background/60",
          !isPreview && "md:grid-cols-4",
        )}
      >
        <Metric
          label="Остаток"
          value={formatInventoryQuantity(batch.qty_remaining)}
          detail={`из ${formatInventoryQuantity(batch.qty_initial)}`}
        />
        <Metric
          label="Срок годности"
          value={formatInventoryDate(batch.expires_at)}
          detail={expiryHint(batch.days_to_expiry)}
          tone={batch.days_to_expiry <= 0 ? "danger" : "default"}
        />
        <Metric
          label="Закупочная стоимость"
          value={formatInventoryMoney(purchaseValue, batch.currency)}
          detail={`${formatInventoryMoney(batch.purchase_price, batch.currency)} за единицу`}
        />
        <Metric
          label="Розничный потенциал"
          value={formatInventoryMoney(saleValue, batch.currency)}
          detail={`маржа ${formatInventoryMoney(marginValue, batch.currency)}`}
        />
      </section>

      <div className="space-y-5 px-4 py-4 sm:px-5">
        {batch.is_blocked && (
          <div className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground">
            <p className="font-medium">Партия недоступна для продажи и списания</p>
            {batch.block_reason && <p className="mt-1">Причина: {batch.block_reason}</p>}
          </div>
        )}

        <section aria-labelledby="batch-properties-heading">
          <h3 id="batch-properties-heading" className="text-sm font-semibold text-foreground">
            Реквизиты партии
          </h3>
          <dl
            className={cn(
              "mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm",
              !isPreview && "md:grid-cols-4",
            )}
          >
            <Field label="Номер партии" value={batch.batch_number ?? "Без номера"} mono />
            <Field
              label="Дата производства"
              value={
                batch.manufactured_at ? formatInventoryDate(batch.manufactured_at) : "Не указана"
              }
            />
            <Field label="Точка" value={batch.branch_name} />
            <Field
              label="Создана"
              value={formatInventoryDateTime(batch.created_at, batch.report_timezone)}
            />
            <Field
              label="Цена закупки"
              value={formatInventoryMoney(batch.purchase_price, batch.currency)}
              mono
            />
            <Field
              label="Цена продажи"
              value={formatInventoryMoney(batch.sale_price, batch.currency)}
              mono
            />
            <Field label="Код товара" value={batch.catalog_id.slice(0, 8)} mono />
            <Field label="Код партии" value={batch.id.slice(0, 8)} mono />
          </dl>
        </section>

        <section aria-labelledby="batch-movements-heading">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 id="batch-movements-heading" className="text-sm font-semibold text-foreground">
              Последние движения
            </h3>
            <span className="text-xs text-foreground-muted">
              {batch.recent_movements.length} операций
            </span>
          </div>
          {batch.recent_movements.length === 0 ? (
            <p className="mt-3 text-sm italic text-foreground-muted">Движений пока нет</p>
          ) : isDesktopLayout && !isPreview ? (
            <MovementTable
              movements={batch.recent_movements}
              reportTimezone={batch.report_timezone}
            />
          ) : (
            <MovementCards
              movements={batch.recent_movements}
              reportTimezone={batch.report_timezone}
            />
          )}
        </section>

        {isPreview ? (
          onOpenFull && (
            <div className="border-t border-border pt-4">
              <Button className="w-full justify-between" variant="secondary" onClick={onOpenFull}>
                Открыть полную карточку
                <ArrowRightIcon />
              </Button>
            </div>
          )
        ) : (
          <div className="flex justify-end border-t border-border pt-4">
            <Button className="min-h-11" variant="ghost" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        )}
      </div>

      <Modal
        open={writeOffOpen}
        onClose={() => setWriteOffOpen(false)}
        title="Списание партии"
        className="max-w-xl"
      >
        <WriteOffForm
          batchId={batch.id}
          maxQty={batch.qty_remaining}
          purchasePrice={batch.purchase_price}
          currency={batch.currency}
          productName={batch.catalog_name}
          batchNumber={batch.batch_number}
          onClose={() => setWriteOffOpen(false)}
        />
      </Modal>
    </div>
  );
}

function MovementTable({
  movements,
  reportTimezone,
}: {
  movements: Movement[];
  reportTimezone: string;
}): JSX.Element {
  return (
    <div className="mt-3">
      <Table>
        <THead>
          <TR>
            <TH>Дата</TH>
            <TH>Операция</TH>
            <TH>Источник</TH>
            <TH className="text-right">Изменение</TH>
          </TR>
        </THead>
        <TBody>
          {movements.map((movement) => (
            <TR key={movement.id}>
              <TD className="whitespace-nowrap">
                {formatInventoryDateTime(movement.created_at, reportTimezone)}
              </TD>
              <TD>{movementLabel[movement.movement_type] ?? movement.movement_type}</TD>
              <TD className="max-w-72 text-xs text-foreground-muted">
                {movement.source_table
                  ? (movementSourceLabel[movement.source_table] ?? movement.source_table)
                  : "Системная операция"}
                {movement.notes && <p className="truncate">{movement.notes}</p>}
              </TD>
              <TD className="text-right">
                <MovementDelta value={movement.qty_delta} />
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function MovementCards({
  movements,
  reportTimezone,
}: {
  movements: Movement[];
  reportTimezone: string;
}): JSX.Element {
  return (
    <div className="mt-3 divide-y divide-border rounded-lg border border-border">
      {movements.map((movement) => (
        <article key={movement.id} className="px-3 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">
                {movementLabel[movement.movement_type] ?? movement.movement_type}
              </p>
              <p className="text-xs text-foreground-muted">
                {formatInventoryDateTime(movement.created_at, reportTimezone)}
              </p>
            </div>
            <MovementDelta value={movement.qty_delta} />
          </div>
          <p className="mt-2 truncate text-xs text-foreground-muted">
            {movement.source_table
              ? (movementSourceLabel[movement.source_table] ?? movement.source_table)
              : "Системная операция"}
            {movement.notes ? ` · ${movement.notes}` : ""}
          </p>
        </article>
      ))}
    </div>
  );
}

function MovementDelta({ value }: { value: string }): JSX.Element {
  const positive = Number(value) > 0;
  return (
    <span
      className={`whitespace-nowrap font-mono text-sm font-semibold tabular-nums ${
        positive ? "text-success-foreground" : "text-danger"
      }`}
    >
      {positive ? "+" : ""}
      {formatInventoryQuantity(value)}
    </span>
  );
}

function ArrowRightIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M4 10h12M11 5l5 5-5 5" />
    </svg>
  );
}

function Metric({
  label,
  value,
  detail,
  tone = "default",
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: "default" | "danger";
}): JSX.Element {
  return (
    <div className="min-w-0 border-b border-r border-border px-4 py-3 last:border-r-0 md:border-b-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p
        className={`mt-1 truncate font-mono text-base font-semibold tabular-nums ${
          tone === "danger" ? "text-danger" : "text-foreground"
        }`}
      >
        {value}
      </p>
      {detail && <p className="mt-0.5 truncate text-xs text-foreground-muted">{detail}</p>}
    </div>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className={`mt-0.5 break-words ${mono ? "font-mono tabular-nums" : ""}`}>{value}</dd>
    </div>
  );
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? false
      : window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
