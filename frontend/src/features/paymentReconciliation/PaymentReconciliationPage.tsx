import { useMemo, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Label,
  PageHeader,
  Pagination,
  SegmentedControl,
  Select,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { describeApiError } from "@/features/foundation/errors";

import { PaymentReconciliationDecisionModal } from "./PaymentReconciliationDecisionModal";
import { usePaymentReconciliationQuery } from "./queries";
import { type PaymentReconciliationItem, type PaymentReconciliationStatus } from "./types";

const PAGE_SIZE = 25;
type QueueStatus = "all" | PaymentReconciliationStatus;

const statusOptions = [
  { value: "all", label: "Все активные" },
  { value: "requires_reconciliation", label: "Нужно решение" },
  { value: "confirmed", label: "Подтверждены" },
] as const;

export default function PaymentReconciliationPage(): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("payment-reconciliation");
  const [branchId, setBranchId] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<"" | "card" | "qr">("");
  const [status, setStatus] = useState<QueueStatus>("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<PaymentReconciliationItem | null>(null);
  const params = useMemo(
    () => ({
      branch_id: branchId || undefined,
      payment_method: paymentMethod || undefined,
      status: status === "all" ? undefined : status,
      page,
      page_size: PAGE_SIZE,
    }),
    [branchId, page, paymentMethod, status],
  );
  const query = usePaymentReconciliationQuery(params);
  const data = query.data;

  const resetFilters = () => {
    setBranchId("");
    setPaymentMethod("");
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Сверка оплат"
        description="Операции карты и QR, результат которых касса не смогла определить автоматически."
        meta={data ? `${data.total} активных операций` : undefined}
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void query.refetch()}
            isLoading={query.isFetching}
          >
            Обновить
          </Button>
        }
      />

      {data ? <QueueSummary data={data.summary} /> : null}

      <div className="flex min-w-0 overflow-x-auto pb-1">
        <SegmentedControl
          value={status}
          options={statusOptions}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
          label="Состояние очереди"
        />
      </div>

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "branch",
            label: "Точка",
            content: (
              <div>
                <Label htmlFor="reconciliation-branch">Точка</Label>
                <Select
                  id="reconciliation-branch"
                  value={branchId}
                  className="w-full sm:w-64"
                  onChange={(event) => {
                    setBranchId(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">Все доступные точки</option>
                  {data?.branches.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchId),
            onClear: () => {
              setBranchId("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "payment_method",
            label: "Способ оплаты",
            content: (
              <div>
                <Label htmlFor="reconciliation-method">Способ оплаты</Label>
                <Select
                  id="reconciliation-method"
                  value={paymentMethod}
                  className="w-full sm:w-48"
                  onChange={(event) => {
                    setPaymentMethod(event.target.value as "" | "card" | "qr");
                    setPage(1);
                  }}
                >
                  <option value="">Карта и QR</option>
                  <option value="card">Карта</option>
                  <option value="qr">QR-код</option>
                </Select>
              </div>
            ),
            active: Boolean(paymentMethod),
            onClear: () => {
              setPaymentMethod("");
              setPage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={resetFilters}
      />

      {query.isLoading ? (
        <SkeletonRows rows={7} />
      ) : query.error ? (
        <div className="rounded-lg border border-danger/30 bg-danger-subtle p-5 text-center">
          <p className="font-semibold text-danger">Очередь не загрузилась</p>
          <p className="mt-1 text-sm text-foreground-secondary">
            {describeApiError(query.error, "Попробуйте обновить данные")}
          </p>
          <Button className="mt-4" size="sm" onClick={() => void query.refetch()}>
            Повторить
          </Button>
        </div>
      ) : data && data.items.length > 0 ? (
        <>
          <QueueTable items={data.items} onResolve={setSelected} />
          <QueueCards items={data.items} onResolve={setSelected} />
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
        </>
      ) : (
        <TableEmpty title="Активных операций нет">
          Все электронные оплаты обработаны. Новые спорные операции появятся здесь автоматически.
        </TableEmpty>
      )}

      {selected ? (
        <PaymentReconciliationDecisionModal
          item={selected}
          onClose={() => setSelected(null)}
          onResolved={() => {
            setSelected(null);
            void query.refetch();
          }}
        />
      ) : null}
    </div>
  );
}

function QueueSummary({
  data,
}: {
  data: {
    requires_reconciliation_count: number;
    requires_reconciliation_amount: string;
    confirmed_count: number;
    confirmed_amount: string;
  };
}): JSX.Element {
  const activeCount = data.requires_reconciliation_count + data.confirmed_count;
  const activeAmount = Number(data.requires_reconciliation_amount) + Number(data.confirmed_amount);
  return (
    <section
      className="grid overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-3"
      aria-label="Сводка сверки"
    >
      <SummaryCell
        label="Нужно решение"
        count={data.requires_reconciliation_count}
        amount={Number(data.requires_reconciliation_amount)}
        tone="warning"
      />
      <SummaryCell
        label="Ждут завершения чека"
        count={data.confirmed_count}
        amount={Number(data.confirmed_amount)}
        tone="success"
      />
      <SummaryCell label="Всего активно" count={activeCount} amount={activeAmount} />
    </section>
  );
}

function SummaryCell({
  label,
  count,
  amount,
  tone,
}: {
  label: string;
  count: number;
  amount: number;
  tone?: "warning" | "success";
}): JSX.Element {
  return (
    <div className="border-b border-border p-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
      <p className="text-sm text-foreground-muted">{label}</p>
      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-2xl font-semibold">{count}</p>
        <Badge tone={tone ?? "neutral"}>{formatMoney(String(amount))} TJS</Badge>
      </div>
    </div>
  );
}

function QueueTable({
  items,
  onResolve,
}: {
  items: PaymentReconciliationItem[];
  onResolve: (item: PaymentReconciliationItem) => void;
}): JSX.Element {
  return (
    <div className="hidden overflow-x-auto rounded-lg border border-border bg-surface md:block">
      <Table>
        <THead>
          <TR>
            <TH>Состояние</TH>
            <TH>Время</TH>
            <TH>Точка и касса</TH>
            <TH>Кассир</TH>
            <TH>Оплата</TH>
            <TH className="text-right">Сумма</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {items.map((item) => (
            <TR key={item.id}>
              <TD>
                <QueueStatus item={item} />
              </TD>
              <TD>
                <p>{formatDateTime(item.reconciliation_started_at)}</p>
                <p className="text-xs text-foreground-muted">
                  {ageLabel(item.reconciliation_started_at)}
                </p>
              </TD>
              <TD>
                <p>{item.branch_name}</p>
                <p className="text-xs text-foreground-muted">{item.register_name}</p>
              </TD>
              <TD>{item.cashier_name ?? "Не указан"}</TD>
              <TD>
                {item.payment_method === "card" ? "Карта" : "QR-код"}
                <p className="text-xs text-foreground-muted">{item.item_count} поз.</p>
              </TD>
              <TD className="text-right font-mono font-semibold">{formatMoney(item.amount)} TJS</TD>
              <TD>
                {item.status === "requires_reconciliation" ? (
                  <Button size="sm" onClick={() => onResolve(item)}>
                    Принять решение
                  </Button>
                ) : null}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function QueueCards({
  items,
  onResolve,
}: {
  items: PaymentReconciliationItem[];
  onResolve: (item: PaymentReconciliationItem) => void;
}): JSX.Element {
  return (
    <div className="space-y-3 md:hidden">
      {items.map((item) => (
        <article key={item.id} className="rounded-lg border border-border bg-surface p-4">
          <div className="flex items-start justify-between gap-3">
            <QueueStatus item={item} />
            <p className="font-mono text-lg font-semibold">{formatMoney(item.amount)} TJS</p>
          </div>
          <p className="mt-3 font-semibold">{item.branch_name}</p>
          <p className="text-sm text-foreground-muted">
            {item.register_name} · {item.cashier_name ?? "Кассир не указан"}
          </p>
          <div className="mt-3 flex justify-between gap-3 text-sm">
            <span>
              {item.payment_method === "card" ? "Карта" : "QR-код"} · {item.item_count} поз.
            </span>
            <span>{ageLabel(item.reconciliation_started_at)}</span>
          </div>
          {item.status === "requires_reconciliation" ? (
            <Button className="mt-4 w-full" onClick={() => onResolve(item)}>
              Принять решение
            </Button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function QueueStatus({ item }: { item: PaymentReconciliationItem }): JSX.Element {
  return item.status === "requires_reconciliation" ? (
    <Badge tone="warning">Нужно решение</Badge>
  ) : (
    <Badge tone="success">Оплата подтверждена</Badge>
  );
}

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date);
}
function formatMoney(value: string): string {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
        amount,
      )
    : value;
}
function ageLabel(value: string): string {
  const started = new Date(value).getTime();
  if (!Number.isFinite(started)) return "Время неизвестно";
  const minutes = Math.max(0, Math.floor((Date.now() - started) / 60_000));
  if (minutes < 1) return "Только что";
  if (minutes < 60) return `${minutes} мин. в очереди`;
  const hours = Math.floor(minutes / 60);
  return `${hours} ч. ${minutes % 60} мин. в очереди`;
}
