import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
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
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import { useSupplierSearchQuery } from "./queries";
import { SupplierForm } from "./SupplierForm";
import { type Supplier } from "./types";

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
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const { data, isLoading, isFetching, error } = useSupplierSearchQuery({
    q,
    is_active: status === "all" ? undefined : status === "active",
    page,
    page_size: PAGE_SIZE,
  });
  const rows = data?.items ?? [];
  const hasFilters = Boolean(q || status !== "active");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Поставщики</h1>
        {canCreate && <Button onClick={() => setCreating(true)}>+ Новый поставщик</Button>}
      </div>

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-64 sm:w-72">
                <Label htmlFor="supplier_search">Поиск</Label>
                <Input
                  id="supplier_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, контакт, телефон или ИНН"
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
                  className="w-40"
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
        onResetValues={() => {
          setQInput("");
          setQ("");
          setStatus("active");
          setPage(1);
        }}
      />

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или выбранные фильтры.</TableEmpty>
        ) : (
          <TableEmpty>Поставщиков пока нет</TableEmpty>
        )
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Название</TH>
                <TH>Контакт</TH>
                <TH>Телефон</TH>
                <TH>Email</TH>
                <TH>Статус</TH>
                {canUpdate && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {rows.map((supplier) => (
                <TR key={supplier.id}>
                  <TD className="font-medium">{supplier.name}</TD>
                  <TD>{supplier.contact_person ?? "—"}</TD>
                  <TD>{supplier.phone ?? "—"}</TD>
                  <TD>{supplier.email ?? "—"}</TD>
                  <TD>
                    {supplier.is_active ? (
                      <Badge tone="success">Активен</Badge>
                    ) : (
                      <Badge tone="neutral">Неактивен</Badge>
                    )}
                  </TD>
                  {canUpdate && (
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={isFetching}
                        onClick={() => setEditing(supplier)}
                      >
                        Изменить
                      </Button>
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} onPage={setPage} />
        </>
      )}

      {(canCreate || canUpdate) && (
        <Modal
          open={creating || editing !== null}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          title={editing ? `Редактирование: ${editing.name}` : "Новый поставщик"}
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
