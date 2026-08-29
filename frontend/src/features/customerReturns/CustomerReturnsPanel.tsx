import { isAxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Checkbox,
  FilterBar,
  FormError,
  Input,
  Label,
  Modal,
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
  Textarea,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { useBranchesQuery } from "@/features/foundation/queries";
import { formatInventoryDate, formatInventoryQuantity } from "@/features/inventory/formatters";
import { describeApiError } from "@/lib/errorMessages";

import { useCustomerReturnsQuery, useResolveCustomerReturn } from "./queries";
import {
  type CustomerReturnDispositionType,
  type CustomerReturnItem,
  type CustomerReturnReasonCode,
  type CustomerReturnStatus,
} from "./types";

const PAGE_SIZE = 25;
const dispositionLabels: Record<CustomerReturnDispositionType, string> = {
  disposed: "Утилизировано",
  supplier_claim: "Возвращено поставщику",
  regulatory_transfer: "Передано уполномоченной организации",
};
const resolutionTypes: CustomerReturnDispositionType[] = [
  "disposed",
  "supplier_claim",
  "regulatory_transfer",
];
const reasonLabels: Record<CustomerReturnReasonCode, string> = {
  damaged: "Повреждение",
  quality_issue: "Проблема качества",
  wrong_item: "Ошибочно выданный товар",
  expired: "Истёк срок годности",
  other: "Другое",
};
const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  dateStyle: "short",
  timeStyle: "short",
});

export function CustomerReturnsPanel(): JSX.Element {
  const { user } = useAuth();
  const canResolve = hasPermission(user, "customer_returns.resolve");
  const canFilterBranches = hasPermission(user, "branches.view");
  const [status, setStatus] = useState<CustomerReturnStatus | "">("pending");
  const [branchId, setBranchId] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<CustomerReturnItem | null>(null);
  const branches = useBranchesQuery(canFilterBranches);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const params = useMemo(
    () => ({
      status: status || undefined,
      branch_id: branchId || undefined,
      search: search || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [branchId, page, search, status],
  );
  const query = useCustomerReturnsQuery(params);
  const filtersActive = Boolean(searchInput || branchId || status !== "pending");
  const reset = () => {
    setStatus("pending");
    setBranchId("");
    setSearchInput("");
    setSearch("");
    setPage(1);
  };

  return (
    <section aria-label="Возвраты покупателей" className="space-y-4">
      {query.data ? <ReturnSummary data={query.data} /> : null}
      <FilterBar>
        <div className="min-w-56 flex-1">
          <Label htmlFor="customer_return_search">Поиск</Label>
          <Input
            id="customer_return_search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Товар, партия или номер чека"
            autoComplete="off"
          />
        </div>
        <div>
          <Label htmlFor="customer_return_status">Статус</Label>
          <Select
            id="customer_return_status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as CustomerReturnStatus | "");
              setPage(1);
            }}
          >
            <option value="">Все</option>
            <option value="pending">Ожидают решения</option>
            <option value="resolved">Завершённые</option>
          </Select>
        </div>
        {canFilterBranches ? (
          <div>
            <Label htmlFor="customer_return_branch">Точка</Label>
            <Select
              id="customer_return_branch"
              value={branchId}
              onChange={(event) => {
                setBranchId(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Все точки</option>
              {branches.data?.map((branch) => (
                <option key={branch.id} value={branch.id}>
                  {branch.name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        <Button variant="ghost" size="sm" disabled={!filtersActive} onClick={reset}>
          Сбросить
        </Button>
      </FilterBar>

      {query.isLoading ? (
        <div role="status" aria-label="Загрузка возвратов">
          <SkeletonRows rows={6} />
        </div>
      ) : query.error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle p-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить возвраты покупателей")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : !query.data?.items.length ? (
        <TableEmpty title={filtersActive ? "Возвраты не найдены" : "Очередь пуста"}>
          {filtersActive
            ? "Измените условия поиска или сбросьте фильтры."
            : "Новых товаров для проверки нет."}
        </TableEmpty>
      ) : (
        <>
          <ReturnTable items={query.data.items} canResolve={canResolve} onResolve={setSelected} />
          <ReturnCards items={query.data.items} canResolve={canResolve} onResolve={setSelected} />
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
        </>
      )}

      {selected ? (
        <ResolveModal
          key={selected.id}
          item={selected}
          onClose={() => setSelected(null)}
          onResolved={() => setSelected(null)}
          onConflict={() => void query.refetch()}
        />
      ) : null}
    </section>
  );
}

function ReturnSummary({
  data,
}: {
  data: { total: number; pending: number; resolved: number };
}): JSX.Element {
  return (
    <div
      className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border"
      aria-label="Сводка по возвратам"
    >
      <Metric label="Всего" value={data.pending + data.resolved} />
      <Metric label="Ожидают решения" value={data.pending} tone="warning" />
      <Metric label="Завершены" value={data.resolved} tone="success" />
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning" | "success";
}): JSX.Element {
  const color =
    tone === "warning"
      ? "text-warning-foreground"
      : tone === "success"
        ? "text-success-foreground"
        : "text-foreground";
  return (
    <div className="bg-surface px-3 py-3 text-center">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${color}`}>
        {value.toLocaleString("ru-RU")}
      </p>
    </div>
  );
}

function Product({ item }: { item: CustomerReturnItem }): JSX.Element {
  const subtitle = [item.catalog_form, item.catalog_dosage].filter(Boolean).join(" · ");
  return (
    <div>
      <p className="font-semibold">{item.catalog_name}</p>
      {subtitle ? <p className="text-xs text-foreground-muted">{subtitle}</p> : null}
    </div>
  );
}

function Status({ item }: { item: CustomerReturnItem }): JSX.Element {
  return item.status === "pending" ? (
    <Badge tone="warning">Ожидает решения</Badge>
  ) : (
    <div>
      <Badge tone="success">Завершён</Badge>
      {item.disposition_type ? (
        <p className="mt-1 text-xs text-foreground-muted">
          {dispositionLabels[item.disposition_type]}
        </p>
      ) : null}
    </div>
  );
}

function ReturnTable({
  items,
  canResolve,
  onResolve,
}: {
  items: CustomerReturnItem[];
  canResolve: boolean;
  onResolve: (item: CustomerReturnItem) => void;
}): JSX.Element {
  return (
    <div className="hidden md:block">
      <Table aria-label="Очередь возвратов покупателей">
        <THead>
          <TR>
            <TH>Товар</TH>
            <TH>Партия</TH>
            <TH>Чеки</TH>
            <TH>Точка и дата</TH>
            <TH>Причина</TH>
            <TH className="text-right">Кол-во</TH>
            <TH>Статус</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {items.map((item) => (
            <TR key={item.id}>
              <TD>
                <Product item={item} />
              </TD>
              <TD>
                <p>{item.batch_number ?? "Без номера"}</p>
                <p className="text-xs text-foreground-muted">
                  до {formatInventoryDate(item.expires_at)}
                </p>
              </TD>
              <TD>
                <p>Возврат №{item.return_receipt_number ?? "—"}</p>
                <p className="text-xs text-foreground-muted">
                  Продажа №{item.parent_receipt_number ?? "—"}
                </p>
              </TD>
              <TD>
                <p>{item.branch_name}</p>
                <p className="text-xs text-foreground-muted">{formatDateTime(item.received_at)}</p>
              </TD>
              <TD className="max-w-56">
                <p className="truncate">{item.refund_reason || "Не указана"}</p>
                {item.refund_comment ? (
                  <p className="truncate text-xs text-foreground-muted">{item.refund_comment}</p>
                ) : null}
              </TD>
              <TD className="text-right font-mono">{formatInventoryQuantity(item.qty)}</TD>
              <TD>
                <Status item={item} />
              </TD>
              <TD>
                {canResolve && item.status === "pending" ? (
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

function ReturnCards({
  items,
  canResolve,
  onResolve,
}: {
  items: CustomerReturnItem[];
  canResolve: boolean;
  onResolve: (item: CustomerReturnItem) => void;
}): JSX.Element {
  return (
    <div className="space-y-3 md:hidden">
      {items.map((item) => (
        <article
          key={item.id}
          className="rounded-lg border border-border bg-surface p-4"
          aria-label={`${item.catalog_name}, возврат покупателя`}
        >
          <div className="flex items-start justify-between gap-3">
            <Product item={item} />
            <Status item={item} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <Info label="Партия" value={item.batch_number ?? "Без номера"} />
            <Info label="Количество" value={formatInventoryQuantity(item.qty)} />
            <Info label="Возвратный чек" value={item.return_receipt_number ?? "—"} />
            <Info label="Исходный чек" value={item.parent_receipt_number ?? "—"} />
            <Info label="Точка" value={item.branch_name} />
            <Info label="Принят" value={formatDateTime(item.received_at)} />
          </dl>
          <p className="mt-3 text-sm">
            <span className="text-foreground-muted">Причина: </span>
            {item.refund_reason || "не указана"}
          </p>
          {canResolve && item.status === "pending" ? (
            <Button className="mt-4 w-full" onClick={() => onResolve(item)}>
              Принять решение
            </Button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date);
}

interface ResolveForm {
  disposition_type: CustomerReturnDispositionType | "";
  reason_code: CustomerReturnReasonCode | "";
  comment: string;
  confirmed: boolean;
}
const resolveSchema = z.object({
  disposition_type: z.enum(["disposed", "supplier_claim", "regulatory_transfer"], {
    required_error: "Выберите выполненное действие",
  }),
  reason_code: z.enum(["damaged", "quality_issue", "wrong_item", "expired", "other"], {
    required_error: "Выберите причину",
  }),
  comment: z.string().trim().max(2000, "Не более 2000 символов"),
  confirmed: z.literal(true, {
    errorMap: () => ({ message: "Подтвердите фактическое выполнение" }),
  }),
});

function ResolveModal({
  item,
  onClose,
  onResolved,
  onConflict,
}: {
  item: CustomerReturnItem;
  onClose: () => void;
  onResolved: () => void;
  onConflict: () => void;
}): JSX.Element {
  const mutation = useResolveCustomerReturn();
  const [operationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<ResolveForm>({
    defaultValues: { disposition_type: "", reason_code: "", comment: "", confirmed: false },
  });
  const submit = form.handleSubmit(async (values) => {
    const parsed = resolveSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string")
          form.setError(field as keyof ResolveForm, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    try {
      await mutation.mutateAsync({
        id: item.id,
        payload: {
          operation_id: operationId,
          disposition_type: parsed.data.disposition_type,
          reason_code: parsed.data.reason_code,
          comment: parsed.data.comment || null,
        },
      });
      onResolved();
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        setTopError(
          "Этот возврат уже обработан другим сотрудником. Закройте окно и проверьте обновлённую очередь.",
        );
        onConflict();
      } else setTopError(describeApiError(error, "Не удалось сохранить решение"));
    }
  });
  return (
    <Modal open onClose={onClose} title="Решение по возвращённому товару" className="max-w-2xl">
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="rounded-lg border border-border bg-background p-3">
          <p className="font-semibold">{item.catalog_name}</p>
          <p className="text-sm text-foreground-muted">
            Партия {item.batch_number ?? "без номера"} · {formatInventoryQuantity(item.qty)} ед.
          </p>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm font-semibold">Фактически выполненное действие</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {resolutionTypes.map((type) => (
              <label
                key={type}
                className="flex min-h-20 cursor-pointer items-start gap-2 rounded-lg border border-border p-3 hover:border-primary/50"
              >
                <input type="radio" value={type} {...form.register("disposition_type")} />
                <span className="text-sm font-medium">{dispositionLabels[type]}</span>
              </label>
            ))}
          </div>
          <FormError>{form.formState.errors.disposition_type?.message}</FormError>
        </fieldset>
        <div>
          <Label htmlFor="customer_return_reason">Причина</Label>
          <Select id="customer_return_reason" {...form.register("reason_code")}>
            <option value="">Выберите причину</option>
            {(Object.keys(reasonLabels) as CustomerReturnReasonCode[]).map((reason) => (
              <option key={reason} value={reason}>
                {reasonLabels[reason]}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.reason_code?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="customer_return_comment">Комментарий</Label>
          <Textarea id="customer_return_comment" rows={3} {...form.register("comment")} />
          <FormError>{form.formState.errors.comment?.message}</FormError>
        </div>
        <div className="rounded-lg border border-warning/40 bg-warning-subtle p-3 text-sm text-warning-foreground">
          <p className="font-medium">Подтверждайте только уже выполненное действие.</p>
          <p className="mt-1">
            После сохранения решение нельзя изменить. Возврат товара в продажу недоступен.
          </p>
        </div>
        <label className="flex cursor-pointer items-start gap-3 text-sm">
          <Checkbox {...form.register("confirmed")} />
          <span>Подтверждаю, что указанное действие фактически выполнено</span>
        </label>
        <FormError>{form.formState.errors.confirmed?.message}</FormError>
        {topError ? (
          <p role="alert" className="text-sm text-danger">
            {topError}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            type="submit"
            isLoading={form.formState.isSubmitting || mutation.isPending}
            disabled={form.formState.isSubmitting || mutation.isPending}
          >
            Подтвердить действие
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
