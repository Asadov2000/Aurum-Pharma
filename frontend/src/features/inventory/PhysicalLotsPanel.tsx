import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  Button,
  Input,
  Label,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { useTenantAccessPolicy } from "@/features/tenantAccess/queries";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { formatInventoryDate } from "./formatters";
import { PhysicalWriteOffWorkspace } from "./PhysicalWriteOffWorkspace";
import {
  physicalLotKeys,
  physicalUnitLabels,
  setPhysicalLotBlocking,
  usePhysicalLots,
  type PhysicalLot,
  type PhysicalLotFilters,
} from "./physicalLots";

const filtersSchema = z.object({
  branch_id: z.string().uuid(),
  q: z.string().trim().max(200),
  status: z.enum(["all", "blocked", "active"]),
  show_empty: z.boolean(),
});
type Filters = z.infer<typeof filtersSchema>;

export function PhysicalLotsPanel(): JSX.Element {
  const { user } = useAuth();
  return (
    <PhysicalLotsWorkspace
      key={JSON.stringify([
        user?.id,
        user?.active_tenant_id,
        user?.permission_scopes,
        user?.permissions,
        user?.support_access?.id,
      ])}
    />
  );
}
function PhysicalLotsWorkspace(): JSX.Element {
  const { user } = useAuth();
  const client = useQueryClient();
  const branches = useBranchesQuery(true);
  const visibleBranches =
    branches.data?.filter(
      (branch) =>
        branch.tenant_id === user?.active_tenant_id &&
        hasPermissionForBranch(user, "batches.view", branch.id),
    ) ?? [];
  const form = useForm<Filters>({
    defaultValues: { branch_id: "", q: "", status: "all", show_empty: false },
  });
  const [params, setParams] = useState<PhysicalLotFilters>({
    branch_id: "",
    page: 1,
    page_size: 25,
    show_empty: false,
  });
  const [selected, setSelected] = useState<PhysicalLot | null>(null);
  const [writeoffLot, setWriteoffLot] = useState<PhysicalLot | null>(null);
  const [writeoffBarrier, setWriteoffBarrier] = useState(true);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [message, setMessage] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [guard] = useState(() =>
    capturePosSessionGuard({ tenantId: user?.active_tenant_id ?? "", userId: user?.id ?? "" }),
  );
  const query = usePhysicalLots(params);
  const policy = useTenantAccessPolicy(
    user?.id,
    user?.active_tenant_id ?? null,
    Boolean(user?.active_tenant_id),
  );
  const writable =
    !user?.support_access &&
    !policy.error &&
    policy.data?.tenant_id === user?.active_tenant_id &&
    policy.data?.mode === "full" &&
    !policy.data.blocked_modules.includes("inventory");
  const canChange = (lot: PhysicalLot) =>
    !query.isFetching && writable && hasPermissionForBranch(user, "batches.update", lot.branch_id);
  const canWriteOff = (lot: PhysicalLot) =>
    !query.isFetching &&
    writable &&
    !lot.is_blocked &&
    BigInt(lot.base_units) > 0n &&
    hasPermissionForBranch(user, "batches.write_off", lot.branch_id);
  const refresh = async (): Promise<void> => {
    await client.invalidateQueries({ queryKey: physicalLotKeys.all });
  };
  const submit = async (): Promise<void> => {
    if (!selected || inFlight.current || !guard() || !canChange(selected)) return;
    inFlight.current = true;
    setBusy(true);
    setMessage(null);
    try {
      await setPhysicalLotBlocking(selected, !selected.is_blocked);
      if (guard())
        setMessage(selected.is_blocked ? "Партия разблокирована." : "Партия заблокирована.");
    } catch (error) {
      if (guard())
        setMessage(
          `${describeApiError(error, "Результат изменения не подтверждён.")} Проверьте состояние партии в обновлённом списке перед новым решением. Если список не загрузился, обновите его.`,
        );
    } finally {
      await refresh();
      inFlight.current = false;
      if (guard()) {
        setBusy(false);
        setSelected(null);
      }
    }
  };
  return (
    <section aria-label="Поштучные партии" className="space-y-4">
      <p className="text-sm text-foreground-secondary">
        Физические остатки в базовых единицах. Блокировка запрещает продажу партии целиком и не
        меняет количество товара.
      </p>
      <PhysicalWriteOffWorkspace
        lot={writeoffLot}
        writable={writable}
        onClose={() => setWriteoffLot(null)}
        onBarrier={setWriteoffBarrier}
      />
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={form.handleSubmit((values) => {
          const result = filtersSchema.safeParse(values);
          if (!result.success) {
            setFilterError("Выберите аптеку; поиск — не более 200 символов.");
            return;
          }
          setFilterError(null);
          setSelected(null);
          setWriteoffLot(null);
          setMessage(null);
          setParams({
            branch_id: result.data.branch_id,
            q: result.data.q || undefined,
            is_blocked: result.data.status === "all" ? undefined : result.data.status === "blocked",
            show_empty: result.data.show_empty,
            page: 1,
            page_size: 25,
          });
        })}
      >
        <div>
          <Label htmlFor="physical-branch">Аптека</Label>
          <Select id="physical-branch" {...form.register("branch_id")}>
            <option value="">Выберите аптеку</option>
            {visibleBranches.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="physical-search">Название или ID партии</Label>
          <Input id="physical-search" maxLength={200} {...form.register("q")} />
        </div>
        <div>
          <Label htmlFor="physical-status">Состояние</Label>
          <Select id="physical-status" {...form.register("status")}>
            <option value="all">Все</option>
            <option value="active">Без блокировки</option>
            <option value="blocked">Заблокированные</option>
          </Select>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" {...form.register("show_empty")} />
          Показывать пустые
        </label>
        <Button type="submit" disabled={busy}>
          Показать партии
        </Button>
      </form>
      {filterError && <p role="alert">{filterError}</p>}
      {branches.error && <p role="alert">Не удалось загрузить аптеки.</p>}
      {message && (
        <p role="status" className="rounded-lg border p-3">
          {message}
        </p>
      )}
      {!params.branch_id ? (
        <p>Выберите аптеку и нажмите «Показать партии».</p>
      ) : query.error ? (
        <div role="alert">
          <p>{describeApiError(query.error, "Не удалось загрузить партии")}</p>
          <Button onClick={() => void query.refetch()}>Обновить список</Button>
        </div>
      ) : query.isFetching ? (
        <SkeletonRows rows={4} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <Table>
              <THead>
                <TR>
                  <TH>Препарат / партия</TH>
                  <TH>Годен до</TH>
                  <TH>Остаток</TH>
                  <TH>Состояние</TH>
                  <TH>Действие</TH>
                </TR>
              </THead>
              <TBody>
                {query.data?.items.map((lot) => (
                  <TR key={lot.lot_id}>
                    <TD>
                      <div>{lot.catalog_name ?? "Без названия"}</div>
                      <div className="text-xs text-foreground-secondary" title={lot.lot_id}>
                        {lot.lot_id.slice(0, 8)}
                      </div>
                    </TD>
                    <TD>{formatInventoryDate(lot.expires_at)}</TD>
                    <TD>
                      <div>
                        {lot.base_units} {physicalUnitLabels[lot.packaging.unit]}
                      </div>
                      <div className="text-xs text-foreground-secondary">
                        Закрытых упаковок: {lot.closed_packs}; во вскрытых упаковках:{" "}
                        {lot.opened_units}
                      </div>
                    </TD>
                    <TD>{lot.is_blocked ? "Заблокирована" : "Не заблокирована"}</TD>
                    <TD>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busy || !canChange(lot)}
                        onClick={() => setSelected(lot)}
                      >
                        {lot.is_blocked ? "Разблокировать" : "Заблокировать"}
                      </Button>
                      {hasPermissionForBranch(user, "batches.write_off", lot.branch_id) && (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy || writeoffBarrier || !canWriteOff(lot)}
                          onClick={() => setWriteoffLot(lot)}
                        >
                          Списать
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
          {query.data?.items.length === 0 && <p>Подходящих поштучных партий нет.</p>}
          <Pagination
            page={params.page}
            pageSize={params.page_size}
            total={query.data?.total ?? 0}
            onPage={(page) => {
              setSelected(null);
              setParams({ ...params, page });
            }}
          />
        </>
      )}
      {selected && (
        <Modal
          open
          title={selected.is_blocked ? "Разблокировать партию" : "Заблокировать партию"}
          onClose={() => {
            if (!busy) setSelected(null);
          }}
        >
          <div className="space-y-4">
            <p>
              {selected.catalog_name ?? "Без названия"} · {selected.lot_id}
            </p>
            <p>
              {selected.is_blocked
                ? "Снять блокировку всей партии? Другие ограничения продажи сохраняются."
                : "Запретить продажу всей партии? Физический остаток не изменится."}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" disabled={busy} onClick={() => setSelected(null)}>
                Отмена
              </Button>
              <Button
                disabled={!canChange(selected)}
                isLoading={busy}
                onClick={() => void submit()}
              >
                Подтвердить
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
