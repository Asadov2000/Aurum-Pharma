import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Modal,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { useSuppliersQuery } from "@/features/suppliers/queries";

import { AddItemForm } from "./AddItemForm";
import { statusLabel, statusTone } from "./labels";
import {
  useAcceptIncoming,
  useDeleteIncomingItem,
  useIncomingDocQuery,
  useRejectIncoming,
} from "./queries";

export function IncomingDetailPage(): JSX.Element {
  const { id } = useParams({ from: "/incoming/$id" });
  const { data: doc, isLoading, error } = useIncomingDocQuery(id);
  const branches = useBranchesQuery(true);
  const suppliers = useSuppliersQuery(true);
  const accept = useAcceptIncoming();
  const reject = useRejectIncoming();
  const deleteItem = useDeleteIncomingItem();
  const [adding, setAdding] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);

  if (isLoading) {
    return <p className="text-sm text-slate-500">Загрузка…</p>;
  }
  if (error || !doc) {
    return (
      <div className="space-y-2">
        <Link to="/incoming">
          <Button variant="ghost" size="sm">
            ← К списку
          </Button>
        </Link>
        <p className="text-sm text-red-600">
          {describeApiError(error, "Не удалось загрузить документ")}
        </p>
      </div>
    );
  }

  const branchName =
    branches.data?.find((b) => b.id === doc.branch_id)?.name ?? doc.branch_id.slice(0, 8);
  const supplierName =
    suppliers.data?.find((s) => s.id === doc.supplier_id)?.name ?? doc.supplier_id.slice(0, 8);
  const isDraft = doc.status === "draft";

  const onAccept = async () => {
    if (!window.confirm("Принять приход? Будут созданы партии в наличии. Действие необратимо.")) return;
    setTopError(null);
    try {
      await accept.mutateAsync(doc.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось принять приход"));
    }
  };

  const onReject = async () => {
    if (!window.confirm("Отклонить приход?")) return;
    setTopError(null);
    try {
      await reject.mutateAsync(doc.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось отклонить"));
    }
  };

  const onDeleteItem = async (itemId: string) => {
    if (!window.confirm("Удалить позицию из прихода?")) return;
    try {
      await deleteItem.mutateAsync({ documentId: doc.id, itemId });
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось удалить позицию"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Link to="/incoming">
          <Button variant="ghost" size="sm">
            ← К списку
          </Button>
        </Link>
        <div className="flex gap-2">
          {isDraft && doc.items.length > 0 && (
            <Button onClick={() => void onAccept()} isLoading={accept.isPending}>
              Принять
            </Button>
          )}
          {isDraft && (
            <Button variant="secondary" onClick={() => void onReject()} isLoading={reject.isPending}>
              Отклонить
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>
              Приход {doc.document_number ? `№ ${doc.document_number}` : "(без номера)"}
            </CardTitle>
            <Badge tone={statusTone[doc.status]}>{statusLabel[doc.status]}</Badge>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Дата" value={new Date(doc.document_date).toLocaleDateString("ru-RU")} />
          <Field label="Точка" value={branchName} />
          <Field label="Поставщик" value={supplierName} />
          <Field
            label="Сумма"
            value={`${Number(doc.total_amount).toFixed(2)} ${doc.currency}`}
          />
          {doc.notes && (
            <div className="col-span-2">
              <p className="text-xs text-slate-500">Комментарий</p>
              <p className="whitespace-pre-wrap">{doc.notes}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {topError && <p className="text-sm text-red-600">{topError}</p>}

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium text-slate-900">Позиции ({doc.items.length})</h2>
        {isDraft && <Button onClick={() => setAdding(true)}>+ Добавить позицию</Button>}
      </div>

      {doc.items.length === 0 ? (
        <TableEmpty>Позиций пока нет</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Партия</TH>
              <TH>Срок годности</TH>
              <TH className="text-right">Кол-во</TH>
              <TH className="text-right">Закуп.</TH>
              <TH className="text-right">Розн.</TH>
              <TH className="text-right">Сумма</TH>
              <TH></TH>
            </TR>
          </THead>
          <TBody>
            {doc.items.map((it) => (
              <TR key={it.id}>
                <TD className="font-mono">
                  {it.batch_number ?? "—"}
                  {it.created_batch_id && (
                    <Badge tone="success" className="ml-2">
                      партия создана
                    </Badge>
                  )}
                </TD>
                <TD>{new Date(it.expires_at).toLocaleDateString("ru-RU")}</TD>
                <TD className="text-right font-mono">{it.qty}</TD>
                <TD className="text-right font-mono">{Number(it.purchase_price).toFixed(2)}</TD>
                <TD className="text-right font-mono">{Number(it.sale_price).toFixed(2)}</TD>
                <TD className="text-right font-mono">
                  {(Number(it.qty) * Number(it.purchase_price)).toFixed(2)}
                </TD>
                <TD className="text-right">
                  {isDraft && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void onDeleteItem(it.id)}
                      isLoading={deleteItem.isPending}
                    >
                      Удалить
                    </Button>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      <Modal open={adding} onClose={() => setAdding(false)} title="Добавить позицию">
        <AddItemForm documentId={doc.id} onClose={() => setAdding(false)} />
      </Modal>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p>{value}</p>
    </div>
  );
}
