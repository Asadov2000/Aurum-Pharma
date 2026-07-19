import { useState } from "react";

import { Badge, Button, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import { movementLabel } from "./labels";
import { useBatchQuery, useMovementsQuery } from "./queries";
import { WriteOffForm } from "./WriteOffForm";

export function BatchDetailModal({
  batchId,
  onClose,
}: {
  batchId: string;
  onClose: () => void;
}): JSX.Element {
  const { user } = useAuth();
  const canWriteOff = hasPermission(user, "batches.write_off");
  const batchQuery = useBatchQuery(batchId);
  const movementsQuery = useMovementsQuery(batchId);
  const [writeOffOpen, setWriteOffOpen] = useState(false);

  if (batchQuery.isLoading) {
    return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  }
  if (batchQuery.error || !batchQuery.data) {
    return (
      <p className="text-sm text-danger">
        {describeApiError(batchQuery.error, "Не удалось загрузить партию")}
      </p>
    );
  }
  const b = batchQuery.data;
  const movements = movementsQuery.data ?? [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Номер партии" value={b.batch_number ?? "—"} mono />
        <Field label="Срок годности" value={new Date(b.expires_at).toLocaleDateString("ru-RU")} />
        <Field
          label="Произведена"
          value={b.manufactured_at ? new Date(b.manufactured_at).toLocaleDateString("ru-RU") : "—"}
        />
        <Field
          label="Цена закупки / продажи"
          value={`${Number(b.purchase_price).toFixed(2)} / ${Number(b.sale_price).toFixed(2)} ${b.currency}`}
        />
        <Field label="Количество" value={`${b.qty_remaining} из ${b.qty_initial}`} />
        <div>
          <p className="text-xs text-foreground-muted">Статус</p>
          {b.is_blocked ? (
            <Badge tone="danger">Заблокирована{b.block_reason ? `: ${b.block_reason}` : ""}</Badge>
          ) : (
            <Badge tone="success">Активна</Badge>
          )}
        </div>
      </div>

      {canWriteOff &&
        !b.is_blocked &&
        Number(b.qty_remaining) > 0 &&
        (writeOffOpen ? (
          <WriteOffForm
            batchId={b.id}
            maxQty={b.qty_remaining}
            onClose={() => setWriteOffOpen(false)}
          />
        ) : (
          <Button variant="secondary" onClick={() => setWriteOffOpen(true)}>
            Списать
          </Button>
        ))}

      <div>
        <p className="mb-2 text-sm font-medium text-foreground-secondary">История движений</p>
        {movementsQuery.isLoading ? (
          <p className="text-sm text-foreground-muted">Загрузка…</p>
        ) : movements.length === 0 ? (
          <p className="text-sm italic text-foreground-muted">Движений пока нет</p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Дата</TH>
                <TH>Тип</TH>
                <TH>Изменение</TH>
                <TH>Источник</TH>
              </TR>
            </THead>
            <TBody>
              {movements.map((m) => {
                const isPositive = Number(m.qty_delta) > 0;
                return (
                  <TR key={m.id}>
                    <TD className="whitespace-nowrap">
                      {new Date(m.created_at).toLocaleString("ru-RU")}
                    </TD>
                    <TD>{movementLabel[m.movement_type] ?? m.movement_type}</TD>
                    <TD
                      className={`font-mono ${isPositive ? "text-success-foreground" : "text-danger"}`}
                    >
                      {isPositive ? "+" : ""}
                      {m.qty_delta}
                    </TD>
                    <TD className="text-xs text-foreground-muted">
                      {m.source_table ?? "—"}
                      {m.notes && <span className="ml-1">· {m.notes}</span>}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </div>

      <div className="flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>
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
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={mono ? "font-mono" : ""}>{value}</p>
    </div>
  );
}
