import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Label,
  Pagination,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { permissionBranchScope } from "@/features/auth/branchPermissions";
import { hasPermission } from "@/features/auth/permissions";
import { useBranchesQuery } from "@/features/foundation/queries";
import { formatInventoryDateTime } from "@/features/inventory/formatters";
import { type PosStorageScope } from "@/features/pos/posStorageScope";
import { describeApiError } from "@/lib/errorMessages";

import { listMixedDispositionHistory, mixedDispositionQuantity } from "./mixedDisposition";
import { mixedDispositionLabels, mixedDispositionReasons } from "./mixedDispositionLabels";

const filters = z.object({ branch_id: z.union([z.string().uuid(), z.literal("")]) });
export function MixedDispositionHistoryPanel({ scope }: { scope: PosStorageScope }): JSX.Element {
  const { user } = useAuth();
  const canView =
    hasPermission(user, "customer_returns.view") &&
    user?.id === scope.userId &&
    user.active_tenant_id === scope.tenantId &&
    !user.support_access;
  const branches = useBranchesQuery(false, canView && hasPermission(user, "branches.view"));
  const form = useForm<z.infer<typeof filters>>({ defaultValues: { branch_id: "" } });
  const branch = form.watch("branch_id");
  const [page, setPage] = useState(1);
  const params = { branch_id: branch || undefined, page, page_size: 25 };
  const query = useQuery({
    queryKey: [
      "customer-returns",
      "mixed-dispositions",
      scope.tenantId,
      scope.userId,
      permissionBranchScope(user, "customer_returns.view"),
      params,
    ],
    queryFn: () => listMixedDispositionHistory(params, scope),
    enabled: filters.safeParse({ branch_id: branch }).success && canView,
    retry: false,
  });
  if (!canView) return <p role="alert">Нет доступа к истории решений.</p>;
  return (
    <section aria-label="История решений по смешанному карантину" className="space-y-4">
      <p className="text-sm text-foreground-secondary">
        Здесь сохранены окончательные решения. Они не изменяют исходные продажи, остатки или
        выплаты.
      </p>
      {hasPermission(user, "branches.view") ? (
        <div className="max-w-sm">
          <Label htmlFor="mixed_disposition_history_branch">Точка истории</Label>
          <Select
            id="mixed_disposition_history_branch"
            {...form.register("branch_id", { onChange: () => setPage(1) })}
          >
            <option value="">Все доступные точки</option>
            {branches.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </div>
      ) : null}
      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.error ? (
        <div role="alert">
          <p>{describeApiError(query.error, "Не удалось загрузить историю решений")}</p>
          <Button variant="secondary" onClick={() => void query.refetch()}>
            Повторить загрузку истории
          </Button>
        </div>
      ) : !query.data?.items.length ? (
        <TableEmpty title="Решений пока нет">Для выбранных точек история пуста.</TableEmpty>
      ) : (
        <>
          <div className="space-y-3">
            {query.data.items.map((item) => (
              <article
                key={item.decision_operation_id}
                className="space-y-2 rounded-lg border border-border p-4"
                aria-label="Сохранённое решение по карантину"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-semibold">{mixedDispositionLabels[item.disposition_type]}</p>
                  <Badge tone="success">Завершено</Badge>
                </div>
                <p>
                  {item.target.inventory_kind === "legacy_pack"
                    ? "Обычные пачки"
                    : "Физические единицы"}
                  : {mixedDispositionQuantity(item.target)}
                </p>
                <p>
                  {item.intake_kind === "zero_return"
                    ? "Приём без выплаты"
                    : "Приём с возвратом оплаты"}
                </p>
                <p>{mixedDispositionReasons[item.reason_code]}</p>
                {item.comment ? (
                  <p className="break-words text-sm text-foreground-secondary">{item.comment}</p>
                ) : null}
                <p className="text-sm text-foreground-muted">
                  Точка:{" "}
                  {branches.data?.find((branch) => branch.id === item.branch_id)?.name ??
                    "Доступная точка"}
                </p>
                {query.data.report_timezone ? (
                  <time className="text-sm text-foreground-muted" dateTime={item.resolved_at}>
                    {formatInventoryDateTime(item.resolved_at, query.data.report_timezone)}
                  </time>
                ) : null}
                <details className="text-sm">
                  <summary className="cursor-pointer">Связанные операции</summary>
                  <dl className="mt-2 space-y-1 break-all">
                    <dt>Решение</dt>
                    <dd>{item.decision_operation_id}</dd>
                    <dt>Приёмка</dt>
                    <dd>{item.target.intake_operation_id}</dd>
                    <dt>Исходная продажа</dt>
                    <dd>{item.parent_sale_id}</dd>
                  </dl>
                </details>
              </article>
            ))}
          </div>
          <Pagination page={page} pageSize={25} total={query.data.total} onPage={setPage} />
        </>
      )}
    </section>
  );
}
