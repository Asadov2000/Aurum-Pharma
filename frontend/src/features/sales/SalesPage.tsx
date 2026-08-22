import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  PageHeader,
  Pagination,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { useBranchesQuery, useRegistersQuery } from "@/features/foundation/queries";
import { paymentMethodLabel } from "@/features/pos/labels";
import { type PaymentMethodRead } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { SaleDetailModal } from "./SaleDetailModal";
import { useSalesQuery } from "./queries";
import { type SaleListItem } from "./types";

const PAGE_SIZE = 50;

const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});

export function SalesPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("sales");
  const canFilterByLocation =
    hasPermission(user, "branches.view") && hasPermission(user, "registers.view");
  const canOpenPos = hasPermission(user, "pos.sell");

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [receiptInput, setReceiptInput] = useState("");
  const [receipt, setReceipt] = useState("");
  const [branchId, setBranchId] = useState("");
  const [registerId, setRegisterId] = useState("");
  const [hasRefund, setHasRefund] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState(1);
  const [openRow, setOpenRow] = useState<SaleListItem | null>(null);

  const branches = useBranchesQuery(true, canFilterByLocation);
  const registers = useRegistersQuery(branchId || null, false, canFilterByLocation);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setReceipt(receiptInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timeout);
  }, [receiptInput]);

  const sales = useSalesQuery({
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    receipt_number: receipt || undefined,
    branch_id: branchId || undefined,
    register_id: registerId || undefined,
    has_refund: hasRefund === "" ? undefined : hasRefund === "true",
    page,
    page_size: PAGE_SIZE,
  });

  const total = sales.data?.total ?? 0;
  const resetPage = () => setPage(1);

  const selectReceiptView = (view: "all" | "with-refund" | "without-refund") => {
    setHasRefund(view === "with-refund" ? "true" : view === "without-refund" ? "false" : "");
    resetPage();
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Чеки"
        description="Продажи, оплаты и связанные возвраты"
        meta={sales.data ? `Найдено: ${total}` : undefined}
        showTitleOnDesktop
        actions={
          <>
            {canOpenPos && (
              <Link
                to="/pos"
                className="inline-flex h-[var(--control-height-lg)] items-center justify-center gap-2 rounded-md bg-primary px-[var(--control-padding-lg)] text-base font-semibold text-primary-foreground shadow-sm transition-colors duration-fast hover:bg-primary/90"
              >
                <RegisterIcon />
                Открыть кассу
              </Link>
            )}
            <Button
              variant="secondary"
              size="lg"
              className="w-[var(--control-height-lg)] px-0"
              aria-label="Обновить чеки"
              title="Обновить"
              isLoading={sales.isFetching}
              onClick={() => void sales.refetch()}
            >
              <RefreshIcon />
            </Button>
          </>
        }
      />

      <ReceiptViews
        active={
          hasRefund === "true" ? "with-refund" : hasRefund === "false" ? "without-refund" : "all"
        }
        total={total}
        onSelect={selectReceiptView}
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "receipt",
            label: "Номер чека",
            content: (
              <div>
                <Label htmlFor="receipt">№ чека</Label>
                <div className="relative">
                  <span
                    className="pointer-events-none absolute inset-y-0 left-3 grid place-items-center text-foreground-muted"
                    aria-hidden="true"
                  >
                    <SearchIcon />
                  </span>
                  <Input
                    id="receipt"
                    value={receiptInput}
                    onChange={(event) => setReceiptInput(event.target.value)}
                    placeholder="000142"
                    className="w-full pl-10 sm:w-44"
                  />
                </div>
              </div>
            ),
            active: Boolean(receiptInput),
            onClear: () => {
              setReceiptInput("");
              setReceipt("");
              resetPage();
            },
            alwaysVisible: true,
          },
          {
            id: "period",
            label: "Период",
            content: (
              <div className="grid w-full grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
                <div>
                  <Label htmlFor="date_from">С</Label>
                  <Input
                    id="date_from"
                    type="date"
                    value={dateFrom}
                    max={dateTo || undefined}
                    onChange={(event) => {
                      setDateFrom(event.target.value);
                      resetPage();
                    }}
                  />
                </div>
                <div>
                  <Label htmlFor="date_to">По</Label>
                  <Input
                    id="date_to"
                    type="date"
                    value={dateTo}
                    min={dateFrom || undefined}
                    onChange={(event) => {
                      setDateTo(event.target.value);
                      resetPage();
                    }}
                  />
                </div>
              </div>
            ),
            active: Boolean(dateFrom || dateTo),
            onClear: () => {
              setDateFrom("");
              setDateTo("");
              resetPage();
            },
            defaultVisible: true,
          },
          {
            id: "branch",
            label: "Точка",
            content: (
              <div>
                <Label htmlFor="branch">Точка</Label>
                <Select
                  id="branch"
                  value={branchId}
                  onChange={(event) => {
                    setBranchId(event.target.value);
                    setRegisterId("");
                    resetPage();
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="">Все</option>
                  {branches.data?.map((branch) => (
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
              setRegisterId("");
              resetPage();
            },
            defaultVisible: true,
            available: canFilterByLocation,
          },
          {
            id: "register",
            label: "Касса",
            content: (
              <div>
                <Label htmlFor="register">Касса</Label>
                <Select
                  id="register"
                  value={registerId}
                  onChange={(event) => {
                    setRegisterId(event.target.value);
                    resetPage();
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="">Все</option>
                  {registers.data?.map((register) => (
                    <option key={register.id} value={register.id}>
                      {register.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(registerId),
            onClear: () => {
              setRegisterId("");
              resetPage();
            },
            available: canFilterByLocation,
          },
          {
            id: "refund",
            label: "Возвраты",
            content: (
              <div>
                <Label htmlFor="has_refund">Связь с возвратом</Label>
                <Select
                  id="has_refund"
                  value={hasRefund}
                  onChange={(event) => {
                    setHasRefund(event.target.value as "" | "true" | "false");
                    resetPage();
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="">Все</option>
                  <option value="true">С возвратом</option>
                  <option value="false">Без возврата</option>
                </Select>
              </div>
            ),
            active: Boolean(hasRefund),
            onClear: () => {
              setHasRefund("");
              resetPage();
            },
          },
        ]}
        onResetValues={() => {
          setReceiptInput("");
          setReceipt("");
          setDateFrom("");
          setDateTo("");
          setBranchId("");
          setRegisterId("");
          setHasRefund("");
          resetPage();
        }}
      />

      {sales.error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
        >
          <span>{describeApiError(sales.error, "Не удалось загрузить чеки")}</span>
          <Button
            variant="secondary"
            size="sm"
            isLoading={sales.isFetching}
            onClick={() => void sales.refetch()}
          >
            Повторить
          </Button>
        </div>
      )}

      {sales.isLoading ? (
        <SkeletonRows rows={8} />
      ) : !sales.data || sales.data.items.length === 0 ? (
        <TableEmpty title="Чеков пока нет" icon={<ReceiptIcon />}>
          {receiptInput || dateFrom || dateTo || branchId || registerId || hasRefund
            ? "По выбранным условиям ничего не найдено. Измените или сбросьте фильтры."
            : "Завершённые продажи появятся здесь — отсюда же оформляется возврат."}
        </TableEmpty>
      ) : (
        <ReceiptList
          items={sales.data.items}
          page={page}
          total={total}
          onPage={setPage}
          onOpen={setOpenRow}
        />
      )}

      {openRow && <SaleDetailModal row={openRow} onClose={() => setOpenRow(null)} />}
    </div>
  );
}

function ReceiptViews({
  active,
  total,
  onSelect,
}: {
  active: "all" | "with-refund" | "without-refund";
  total: number;
  onSelect: (view: "all" | "with-refund" | "without-refund") => void;
}): JSX.Element {
  return (
    <section
      className="flex min-h-14 items-end gap-1 overflow-x-auto rounded-lg border border-border bg-surface px-3"
      aria-label="Представление чеков"
    >
      <ViewButton
        label="Все чеки"
        active={active === "all"}
        count={active === "all" ? total : undefined}
        onClick={() => onSelect("all")}
      />
      <ViewButton
        label="С возвратом"
        active={active === "with-refund"}
        count={active === "with-refund" ? total : undefined}
        onClick={() => onSelect("with-refund")}
      />
      <ViewButton
        label="Без возврата"
        active={active === "without-refund"}
        count={active === "without-refund" ? total : undefined}
        onClick={() => onSelect("without-refund")}
      />
    </section>
  );
}

function ViewButton({
  label,
  active,
  count,
  onClick,
}: {
  label: string;
  active: boolean;
  count?: number;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "relative inline-flex min-h-14 shrink-0 items-center gap-2 px-3 text-sm font-medium transition-colors duration-fast",
        active ? "text-primary" : "text-foreground-secondary hover:text-foreground",
      )}
      onClick={onClick}
    >
      {label}
      {count !== undefined && (
        <span className="rounded-md bg-background px-2 py-0.5 font-mono text-xs text-foreground">
          {count}
        </span>
      )}
      {active && <span className="absolute inset-x-2 bottom-0 h-0.5 bg-primary" aria-hidden />}
    </button>
  );
}

const desktopReceiptGrid =
  "lg:grid lg:grid-cols-[5.5rem_9rem_minmax(8rem,1fr)_minmax(7rem,.8fr)_7.5rem_minmax(8rem,auto)_2rem] 2xl:grid-cols-[5.5rem_9.5rem_minmax(8rem,1fr)_minmax(9rem,1fr)_minmax(10rem,1.2fr)_minmax(7rem,.8fr)_7.5rem_minmax(8rem,auto)_2rem]";

function ReceiptList({
  items,
  page,
  total,
  onPage,
  onOpen,
}: {
  items: SaleListItem[];
  page: number;
  total: number;
  onPage: (page: number) => void;
  onOpen: (sale: SaleListItem) => void;
}): JSX.Element {
  return (
    <section
      className="overflow-hidden rounded-lg border border-border bg-surface"
      aria-label="Чеки"
    >
      <div
        className={cn(
          desktopReceiptGrid,
          "hidden min-h-11 items-center gap-3 border-b border-border bg-background px-4 text-xs font-semibold text-foreground-muted lg:grid",
        )}
        aria-hidden="true"
      >
        <span>№ чека</span>
        <span>Дата и время</span>
        <span>Кассир</span>
        <span className="hidden 2xl:block">Точка · касса</span>
        <span className="hidden 2xl:block">Товары</span>
        <span>Оплата</span>
        <span className="text-right">Сумма</span>
        <span>Статус</span>
        <span />
      </div>

      <div className="divide-y divide-border">
        {items.map((sale) => (
          <ReceiptRow key={sale.id} sale={sale} onOpen={() => onOpen(sale)} />
        ))}
      </div>

      <footer className="border-t border-border px-4 py-3">
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={onPage} />
      </footer>
    </section>
  );
}

function ReceiptRow({ sale, onOpen }: { sale: SaleListItem; onOpen: () => void }): JSX.Element {
  const receiptNumber = sale.receipt_number ?? "—";
  const amount = formatSaleAmount(sale);
  const payment = formatPayments(sale.payment_methods);
  const date = formatCompletedAt(sale.completed_at);
  const location = [sale.branch_name, sale.register_name].filter(Boolean).join(" · ") || "—";

  return (
    <button
      type="button"
      aria-label={`Открыть чек № ${receiptNumber}`}
      className="block w-full text-left transition-colors duration-fast hover:bg-foreground/[0.025] focus-visible:bg-foreground/[0.025]"
      onClick={onOpen}
    >
      <div className="space-y-2 px-4 py-3 lg:hidden">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-sm font-semibold text-foreground">№ {receiptNumber}</p>
            <p className="mt-0.5 truncate text-xs text-foreground-muted">
              {date} · {sale.cashier_name ?? "Кассир не указан"}
            </p>
          </div>
          <p
            className={cn(
              "shrink-0 font-mono text-sm font-semibold tabular-nums",
              sale.is_refund ? "text-danger" : "text-foreground",
            )}
          >
            {amount}
          </p>
        </div>
        <p className="truncate text-xs text-foreground-secondary">{location}</p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <ReceiptStatus sale={sale} />
          <span className="text-xs text-foreground-secondary">{payment}</span>
        </div>
      </div>

      <div className={cn(desktopReceiptGrid, "hidden min-h-16 items-center gap-3 px-4 lg:grid")}>
        <span className="font-mono text-sm font-semibold text-foreground">{receiptNumber}</span>
        <span className="text-sm text-foreground-secondary">{date}</span>
        <span className="truncate text-sm text-foreground">{sale.cashier_name ?? "—"}</span>
        <span className="hidden truncate text-sm text-foreground-secondary 2xl:block">
          {location}
        </span>
        <span className="hidden truncate text-sm text-foreground-secondary 2xl:block">
          {sale.items_summary || "—"}
        </span>
        <span className="truncate text-sm text-foreground-secondary">{payment}</span>
        <span
          className={cn(
            "text-right font-mono text-sm font-semibold tabular-nums",
            sale.is_refund ? "text-danger" : "text-foreground",
          )}
        >
          {amount}
        </span>
        <ReceiptStatus sale={sale} />
        <span className="text-foreground-muted" aria-hidden="true">
          <ChevronIcon />
        </span>
      </div>
    </button>
  );
}

function ReceiptStatus({ sale }: { sale: SaleListItem }): JSX.Element {
  return (
    <span className="flex min-w-0 flex-wrap gap-1">
      {sale.is_refund ? (
        <Badge tone="warning">Возврат</Badge>
      ) : (
        <Badge tone="success">Продажа</Badge>
      )}
      {sale.has_refund && (
        <Badge tone="info">
          {sale.refund_receipt_number ? `Возврат №${sale.refund_receipt_number}` : "Есть возврат"}
        </Badge>
      )}
      {sale.is_refund && sale.parent_receipt_number && (
        <Badge tone="info">к чеку №{sale.parent_receipt_number}</Badge>
      )}
    </span>
  );
}

function formatCompletedAt(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${dateFormatter.format(date)} · ${timeFormatter.format(date)}`;
}

function formatSaleAmount(sale: SaleListItem): string {
  const value = Number(sale.total_amount);
  const amount = Number.isFinite(value)
    ? moneyFormatter.format(Math.abs(value))
    : sale.total_amount;
  return `${sale.is_refund ? "−" : ""}${amount} ${sale.currency}`;
}

function formatPayments(methods: string[]): string {
  return (
    methods
      .map((method) => paymentMethodLabel[method as PaymentMethodRead] ?? method)
      .join(" + ") || "—"
  );
}

function RegisterIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M5 10h14v9H5z" />
      <path d="M7 10V5h10v5M8 14h3M15 14h1M8 17h8" />
    </svg>
  );
}

function RefreshIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6v5h-5" />
      <path d="M4 18v-5h5" />
      <path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5" />
    </svg>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </svg>
  );
}

function ReceiptIcon(): JSX.Element {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </svg>
  );
}

function ChevronIcon(): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}
