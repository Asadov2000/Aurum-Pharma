import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
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
import { useBranchesQuery } from "@/features/foundation/queries";
import {
  formatInventoryDate,
  formatInventoryDateTime,
  formatInventoryQuantity,
} from "@/features/inventory/formatters";
import { describeApiError } from "@/lib/errorMessages";
import { refundReasonLabel } from "@/lib/refundReasons";

import { useMixedCustomerReturnsQuery } from "./queries";
import {
  MixedDispositionWorkspace,
  type MixedDispositionActions,
} from "./MixedDispositionWorkspace";
import { type MixedCustomerReturnItem } from "./types";

const PAGE_SIZE = 25;
const filterSchema = z.object({
  search: z.string().trim().max(160),
  branch_id: z.union([z.string().uuid(), z.literal("")]),
});
type Filters = z.infer<typeof filterSchema>;

export function MixedCustomerReturnsPanel(): JSX.Element {
  const { user } = useAuth();
  return (
    <MixedDispositionWorkspace key={`${user?.id}:${user?.active_tenant_id}`}>
      {(actions) => <MixedCustomerReturnsQueue {...actions} />}
    </MixedDispositionWorkspace>
  );
}

function MixedCustomerReturnsQueue(actions: MixedDispositionActions): JSX.Element {
  const { user } = useAuth();
  const canFilterBranches = hasPermission(user, "branches.view");
  const branches = useBranchesQuery(canFilterBranches);
  const preferenceKey = useFilterPreferenceKey("mixed-customer-returns");
  const form = useForm<Filters>({ defaultValues: { search: "", branch_id: "" } });
  const searchInput = form.watch("search");
  const branchId = form.watch("branch_id");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const timer = setTimeout(() => {
      const parsed = filterSchema.safeParse({ search: searchInput, branch_id: branchId });
      if (parsed.success) {
        setSearch(parsed.data.search);
        setPage(1);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, branchId]);

  const query = useMixedCustomerReturnsQuery({
    branch_id: branchId || undefined,
    search: search || undefined,
    page,
    page_size: PAGE_SIZE,
  });
  const filtersActive = Boolean(searchInput || branchId);
  const clearSearch = (): void => {
    form.setValue("search", "");
    setSearch("");
    setPage(1);
  };
  const clearBranch = (): void => {
    form.setValue("branch_id", "");
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <p className="rounded-lg border border-border bg-surface-raised p-4 text-sm text-foreground-secondary">
        Товар из смешанных продаж изолирован от продажи. Количество в пачках и физических единицах
        учитывается отдельно. Решение фиксирует уже выполненное действие для всего принятого объёма.
      </p>
      <ConfigurableFilterBar
        preferenceKey={preferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            activeLabel: searchInput,
            alwaysVisible: true,
            active: Boolean(searchInput),
            onClear: clearSearch,
            content: (
              <div className="min-w-56 flex-1">
                <Label htmlFor="mixed_return_search">Поиск</Label>
                <Input
                  id="mixed_return_search"
                  {...form.register("search")}
                  maxLength={160}
                  placeholder="Товар, партия или номер чека"
                  autoComplete="off"
                />
              </div>
            ),
          },
          {
            id: "branch",
            label: "Точка",
            available: canFilterBranches,
            activeLabel: branches.data?.find((branch) => branch.id === branchId)?.name,
            active: Boolean(branchId),
            onClear: clearBranch,
            content: (
              <div>
                <Label htmlFor="mixed_return_branch">Точка</Label>
                <Select
                  id="mixed_return_branch"
                  {...form.register("branch_id", { onChange: () => setPage(1) })}
                >
                  <option value="">Все точки</option>
                  {branches.data?.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
          },
        ]}
        onResetValues={() => {
          form.reset({ search: "", branch_id: "" });
          setSearch("");
          setPage(1);
        }}
      />
      {query.isLoading ? (
        <div role="status" aria-label="Загрузка смешанных возвратов">
          <SkeletonRows rows={6} />
        </div>
      ) : query.error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle p-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить смешанные возвраты")}</p>
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
            : "Смешанных возвратов в карантине нет."}
        </TableEmpty>
      ) : (
        <>
          <MixedReturnTable items={query.data.items} actions={actions} />
          <MixedReturnCards items={query.data.items} actions={actions} />
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}

function itemKey(item: MixedCustomerReturnItem): string {
  return `${item.operation_id}:${item.line_id}:${item.batch_id ?? item.lot_id}`;
}
function quantity(item: MixedCustomerReturnItem): string {
  if (item.inventory_kind === "physical_unit")
    return `${formatInventoryQuantity(item.base_units)} ${item.unit_label}`;
  const [whole, fraction] = item.legacy_quantity.split(".");
  const integer = new Intl.NumberFormat("ru-RU").format(BigInt(whole!));
  const decimal = fraction!.replace(/0+$/, "");
  return `${integer}${decimal ? `,${decimal}` : ""} уп.`;
}
function receivedAt(item: MixedCustomerReturnItem): string {
  return formatInventoryDateTime(item.received_at, item.report_timezone);
}
function Intake({ item }: { item: MixedCustomerReturnItem }): JSX.Element {
  return item.intake_kind === "zero_return" ? (
    <p>Принят без выплаты</p>
  ) : (
    <p>Возврат №{item.return_receipt_number ?? "—"}</p>
  );
}
function Reason({ item }: { item: MixedCustomerReturnItem }): JSX.Element {
  return (
    <div>
      <p>{refundReasonLabel(item.refund_reason)}</p>
      {item.refund_comment ? (
        <p className="mt-1 text-xs text-foreground-muted break-words">{item.refund_comment}</p>
      ) : null}
    </div>
  );
}
function MixedReturnTable({
  items,
  actions,
}: {
  items: MixedCustomerReturnItem[];
  actions: MixedDispositionActions;
}): JSX.Element {
  return (
    <div className="hidden md:block">
      <Table aria-label="Очередь смешанных возвратов">
        <THead>
          <TR>
            <TH>Товар</TH>
            <TH>Партия</TH>
            <TH>Приём и исходный чек</TH>
            <TH>Точка и дата</TH>
            <TH>Причина</TH>
            <TH className="text-right">Количество</TH>
            <TH>Статус</TH>
            <TH>Действие</TH>
          </TR>
        </THead>
        <TBody>
          {items.map((item) => (
            <TR key={itemKey(item)}>
              <TD>
                <p className="font-semibold">{item.catalog_name}</p>
                <p className="text-xs text-foreground-muted">
                  {item.inventory_kind === "legacy_pack"
                    ? "Пачки с дробным количеством"
                    : "Физические единицы"}
                </p>
              </TD>
              <TD>
                <p>{item.batch_number ?? "Без номера"}</p>
                <p className="text-xs text-foreground-muted">
                  до {formatInventoryDate(item.expires_at)}
                </p>
              </TD>
              <TD>
                <Intake item={item} />
                <p className="text-xs text-foreground-muted">
                  Продажа №{item.parent_receipt_number ?? "—"}
                </p>
              </TD>
              <TD>
                <p>{item.branch_name}</p>
                <p className="text-xs text-foreground-muted">{receivedAt(item)}</p>
              </TD>
              <TD className="max-w-56">
                <Reason item={item} />
              </TD>
              <TD className="text-right font-mono whitespace-nowrap">{quantity(item)}</TD>
              <TD>
                <Badge tone="warning">В карантине</Badge>
              </TD>
              <TD>
                {actions.canResolve(item) ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={actions.blocked}
                    onClick={() => actions.onResolve(item)}
                  >
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
function MixedReturnCards({
  items,
  actions,
}: {
  items: MixedCustomerReturnItem[];
  actions: MixedDispositionActions;
}): JSX.Element {
  return (
    <div className="space-y-3 md:hidden">
      {items.map((item) => (
        <article
          key={itemKey(item)}
          className="rounded-lg border border-border bg-surface p-4"
          aria-label={`${item.catalog_name}, смешанный возврат`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1 break-words">
              <p className="font-semibold">{item.catalog_name}</p>
              <p className="text-xs text-foreground-muted">
                {item.inventory_kind === "legacy_pack"
                  ? "Пачки с дробным количеством"
                  : "Физические единицы"}
              </p>
            </div>
            <Badge tone="warning">В карантине</Badge>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <Info label="Партия" value={item.batch_number ?? "Без номера"} />
            <Info label="Годен до" value={formatInventoryDate(item.expires_at)} />
            <Info label="Количество" value={quantity(item)} />
            <Info label="Исходный чек" value={item.parent_receipt_number ?? "—"} />
            <Info label="Точка" value={item.branch_name} />
            <Info label="Принят" value={receivedAt(item)} />
          </dl>
          <div className="mt-3 text-sm">
            <Intake item={item} />
          </div>
          <div className="mt-3 text-sm">
            <span className="text-foreground-muted">Причина: </span>
            <Reason item={item} />
          </div>
          {actions.canResolve(item) ? (
            <Button
              className="mt-3"
              variant="secondary"
              disabled={actions.blocked}
              onClick={() => actions.onResolve(item)}
            >
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
    <div className="min-w-0 break-words">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
