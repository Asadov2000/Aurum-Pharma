import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  Modal,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasAnyPermission, hasPermission } from "@/features/auth/permissions";
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
  const { user } = useAuth();
  const canEdit = hasPermission(user, "incoming.create");
  const canDiscoverBranches = hasAnyPermission(user, [
    "branches.view",
    "registers.view",
    "pos.shift_open",
    "pos.shift_close",
    "pos.sell",
    "incoming.view",
    "incoming.create",
  ]);
  const canViewSuppliers = hasPermission(user, "suppliers.view");
  const { id } = useParams({ from: "/incoming/$id" });
  const { data: doc, isLoading, error } = useIncomingDocQuery(id);
  const branches = useBranchesQuery(true, canDiscoverBranches);
  const suppliers = useSuppliersQuery(true, canViewSuppliers);
  const accept = useAcceptIncoming();
  const reject = useRejectIncoming();
  const deleteItem = useDeleteIncomingItem();
  const [adding, setAdding] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [docAction, setDocAction] = useState<"accept" | "reject" | null>(null);
  const [pendingDeleteItemId, setPendingDeleteItemId] = useState<string | null>(null);
  const [deleteItemError, setDeleteItemError] = useState<string | null>(null);

  if (isLoading) {
    return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  }
  if (error || !doc) {
    return (
      <div className="space-y-2">
        <Link to="/incoming">
          <Button variant="ghost" size="sm">
            ← К списку
          </Button>
        </Link>
        <p className="text-sm text-danger">
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
    if (!canEdit) return;
    setTopError(null);
    try {
      await accept.mutateAsync(doc.id);
      setDocAction(null);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось принять приход"));
    }
  };

  const onReject = async () => {
    if (!canEdit) return;
    setTopError(null);
    try {
      await reject.mutateAsync(doc.id);
      setDocAction(null);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось отклонить"));
    }
  };

  const onDeleteItem = async () => {
    if (!pendingDeleteItemId || !canEdit) return;
    setDeleteItemError(null);
    try {
      await deleteItem.mutateAsync({ documentId: doc.id, itemId: pendingDeleteItemId });
      setPendingDeleteItemId(null);
    } catch (err) {
      setDeleteItemError(describeApiError(err, "Не удалось удалить позицию"));
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
          {canEdit && isDraft && doc.items.length > 0 && (
            <Button onClick={() => setDocAction("accept")} isLoading={accept.isPending}>
              Принять
            </Button>
          )}
          {canEdit && isDraft && (
            <Button
              variant="secondary"
              onClick={() => setDocAction("reject")}
              isLoading={reject.isPending}
            >
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
          <Field label="Сумма" value={`${Number(doc.total_amount).toFixed(2)} ${doc.currency}`} />
          {doc.notes && (
            <div className="col-span-2">
              <p className="text-xs text-foreground-muted">Комментарий</p>
              <p className="whitespace-pre-wrap">{doc.notes}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {topError && <p className="text-sm text-danger">{topError}</p>}

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium text-foreground">Позиции ({doc.items.length})</h2>
        {canEdit && isDraft && <Button onClick={() => setAdding(true)}>+ Добавить позицию</Button>}
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
              {canEdit && <TH></TH>}
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
                {canEdit && (
                  <TD className="text-right">
                    {isDraft && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setDeleteItemError(null);
                          setPendingDeleteItemId(it.id);
                        }}
                        isLoading={deleteItem.isPending}
                      >
                        Удалить
                      </Button>
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {canEdit && (
        <Modal open={adding} onClose={() => setAdding(false)} title="Добавить позицию">
          <AddItemForm documentId={doc.id} onClose={() => setAdding(false)} />
        </Modal>
      )}
      {canEdit && (
        <ConfirmDialog
          open={docAction === "accept"}
          title="Принять приход"
          message="Будут созданы партии в наличии. Действие необратимо."
          confirmLabel="Принять"
          isLoading={accept.isPending}
          onConfirm={() => void onAccept()}
          onCancel={() => setDocAction(null)}
        />
      )}
      {canEdit && (
        <ConfirmDialog
          open={docAction === "reject"}
          title="Отклонить приход"
          message="Документ останется в истории со статусом отклонённого."
          confirmLabel="Отклонить"
          variant="danger"
          isLoading={reject.isPending}
          onConfirm={() => void onReject()}
          onCancel={() => setDocAction(null)}
        />
      )}
      {canEdit && (
        <ConfirmDialog
          open={pendingDeleteItemId !== null}
          title="Удалить позицию"
          message={
            <>
              Позиция будет удалена из черновика прихода.
              {deleteItemError && <span className="mt-2 block text-danger">{deleteItemError}</span>}
            </>
          }
          confirmLabel="Удалить"
          variant="danger"
          isLoading={deleteItem.isPending}
          onConfirm={() => void onDeleteItem()}
          onCancel={() => {
            setPendingDeleteItemId(null);
            setDeleteItemError(null);
          }}
        />
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p>{value}</p>
    </div>
  );
}
