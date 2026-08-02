import { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
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
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import { useSupplierSearchQuery } from "./queries";
import { SupplierDetailModal } from "./SupplierDetailModal";
import { SupplierForm } from "./SupplierForm";
import { type Supplier, type SupplierSearchSummary } from "./types";

const PAGE_SIZE = 25;

type StatusFilter = "active" | "inactive" | "all";

export function SuppliersPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("suppliers");
  const canCreate = hasPermission(user, "suppliers.create");
  const canUpdate = hasPermission(user, "suppliers.update");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Supplier | null>(null);
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const params = useMemo(
    () => ({
      q: q || undefined,
      is_active: status === "all" ? undefined : status === "active",
      page,
      page_size: PAGE_SIZE,
    }),
    [page, q, status],
  );
  const query = useSupplierSearchQuery(params);
  const rows = query.data?.items ?? [];
  const hasFilters = Boolean(qInput.trim() || status !== "active");

  const resetFilters = () => {
    setQInput("");
    setQ("");
    setStatus("active");
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Поставщики"
        description="Контакты, реквизиты и возвраты по каждой компании-партнёру."
        meta={
          query.data ? (
            <span aria-live="polite">
              {query.data.total} найдено
              {query.isFetching && !query.isLoading ? " · обновление" : ""}
            </span>
          ) : undefined
        }
        actions={
          canCreate ? <Button onClick={() => setCreating(true)}>Новый поставщик</Button> : undefined
        }
      />

      {query.data && <SupplierSummary summary={query.data.summary} />}

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-full sm:w-80">
                <Label htmlFor="supplier_search">Поиск</Label>
                <Input
                  id="supplier_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, контакт, телефон или ИНН"
                  autoComplete="off"
                />
              </div>
            ),
            active: Boolean(qInput.trim()),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="supplier_status_filter">Статус</Label>
                <Select
                  id="supplier_status_filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as StatusFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="active">Активные</option>
                  <option value="inactive">Неактивные</option>
                  <option value="all">Все</option>
                </Select>
              </div>
            ),
            active: status !== "active",
            onClear: () => {
              setStatus("active");
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
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить поставщиков")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : rows.length === 0 ? (
        <TableEmpty
          title={hasFilters ? "Поставщики не найдены" : "Поставщиков пока нет"}
          action={
            hasFilters ? (
              <Button variant="secondary" size="sm" onClick={resetFilters}>
                Сбросить фильтры
              </Button>
            ) : canCreate ? (
              <Button size="sm" onClick={() => setCreating(true)}>
                Добавить поставщика
              </Button>
            ) : undefined
          }
        >
          {hasFilters
            ? "Измените запрос или верните стандартный набор фильтров."
            : "Добавьте первую компанию, чтобы оформлять приходы и возвраты."}
        </TableEmpty>
      ) : (
        <>
          {isDesktopLayout ? (
            <SupplierTable items={rows} onOpen={setDetail} />
          ) : (
            <div className="grid grid-cols-1 gap-3">
              {rows.map((supplier) => (
                <SupplierCard key={supplier.id} supplier={supplier} onOpen={setDetail} />
              ))}
            </div>
          )}
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={query.data?.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={detail?.name ?? "Карточка поставщика"}
        className="max-w-6xl"
        bodyClassName="p-0 sm:p-0"
      >
        {detail && (
          <SupplierDetailModal
            supplier={detail}
            onClose={() => setDetail(null)}
            onEdit={(supplier) => {
              setDetail(null);
              setEditing(supplier);
            }}
          />
        )}
      </Modal>

      {(canCreate || canUpdate) && (
        <Modal
          open={creating || editing !== null}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          title={editing ? `Редактирование: ${editing.name}` : "Новый поставщик"}
          className="max-w-2xl"
        >
          <SupplierForm
            supplier={editing}
            onClose={() => {
              setCreating(false);
              setEditing(null);
            }}
          />
        </Modal>
      )}
    </div>
  );
}

function SupplierSummary({ summary }: { summary: SupplierSearchSummary }): JSX.Element {
  const contactCoverage = summary.all_count
    ? Math.round((summary.with_contact_count / summary.all_count) * 100)
    : 0;
  return (
    <section
      aria-label="Сводка по поставщикам"
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface md:grid-cols-4"
    >
      <SummaryMetric label="Всего" value={summary.all_count} />
      <SummaryMetric label="Активные" value={summary.active_count} tone="success" />
      <SummaryMetric
        label="Неактивные"
        value={summary.inactive_count}
        tone={summary.inactive_count > 0 ? "muted" : "default"}
      />
      <SummaryMetric
        label="Есть контакты"
        value={summary.with_contact_count}
        detail={`${contactCoverage}% справочника`}
      />
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  detail,
  tone = "default",
}: {
  label: string;
  value: number;
  detail?: string;
  tone?: "default" | "success" | "muted";
}): JSX.Element {
  const toneClass =
    tone === "success"
      ? "text-success-foreground"
      : tone === "muted"
        ? "text-foreground-secondary"
        : "text-foreground";
  return (
    <div className="min-w-0 border-b border-r border-border px-4 py-3 last:border-r-0 md:border-b-0">
      <p className="text-xs font-medium text-foreground-muted">{label}</p>
      <p className={`mt-1 font-mono text-lg font-semibold tabular-nums ${toneClass}`}>
        {value.toLocaleString("ru-RU")}
      </p>
      {detail && <p className="mt-0.5 truncate text-xs text-foreground-muted">{detail}</p>}
    </div>
  );
}

function SupplierTable({
  items,
  onOpen,
}: {
  items: Supplier[];
  onOpen: (supplier: Supplier) => void;
}): JSX.Element {
  return (
    <Table>
      <THead>
        <TR>
          <TH>Поставщик</TH>
          <TH>Реквизиты</TH>
          <TH>Контакт</TH>
          <TH>Связь</TH>
          <TH>Статус</TH>
          <TH className="text-right">Карточка</TH>
        </TR>
      </THead>
      <TBody>
        {items.map((supplier) => (
          <TR key={supplier.id}>
            <TD>
              <button
                type="button"
                className="max-w-72 text-left font-medium text-foreground hover:text-primary hover:underline"
                onClick={() => onOpen(supplier)}
              >
                {supplier.name}
              </button>
              {supplier.legal_name && (
                <p className="mt-0.5 max-w-72 truncate text-xs text-foreground-muted">
                  {supplier.legal_name}
                </p>
              )}
            </TD>
            <TD>
              <p className="font-mono text-xs tabular-nums">
                {supplier.inn_or_tin || "ИНН не указан"}
              </p>
              {supplier.address && (
                <p className="mt-1 max-w-56 truncate text-xs text-foreground-muted">
                  {supplier.address}
                </p>
              )}
            </TD>
            <TD>{supplier.contact_person || "—"}</TD>
            <TD>
              <p>{supplier.phone || "—"}</p>
              {supplier.email && (
                <p className="mt-0.5 max-w-52 truncate text-xs text-foreground-muted">
                  {supplier.email}
                </p>
              )}
            </TD>
            <TD>
              <Badge tone={supplier.is_active ? "success" : "neutral"}>
                {supplier.is_active ? "Активен" : "Неактивен"}
              </Badge>
            </TD>
            <TD className="text-right">
              <Button variant="ghost" size="sm" onClick={() => onOpen(supplier)}>
                Открыть
              </Button>
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function SupplierCard({
  supplier,
  onOpen,
}: {
  supplier: Supplier;
  onOpen: (supplier: Supplier) => void;
}): JSX.Element {
  return (
    <article className="rounded-lg border border-border bg-surface px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-words text-base font-semibold text-foreground">{supplier.name}</h2>
          <p className="mt-1 truncate text-sm text-foreground-muted">
            {supplier.contact_person || supplier.legal_name || "Контакт не указан"}
          </p>
        </div>
        <Badge tone={supplier.is_active ? "success" : "neutral"}>
          {supplier.is_active ? "Активен" : "Неактивен"}
        </Badge>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <CardField label="Телефон" value={supplier.phone || "—"} />
        <CardField label="Email" value={supplier.email || "—"} />
        <CardField label="ИНН / TIN" value={supplier.inn_or_tin || "—"} mono />
        <CardField label="Адрес" value={supplier.address || "—"} />
      </div>
      <Button className="mt-4 min-h-11 w-full" variant="secondary" onClick={() => onOpen(supplier)}>
        Открыть карточку
      </Button>
    </article>
  );
}

function CardField({
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
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={`mt-0.5 truncate ${mono ? "font-mono tabular-nums" : ""}`}>{value}</p>
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
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);

  return matches;
}
