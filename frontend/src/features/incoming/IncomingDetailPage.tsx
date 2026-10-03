import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useBlocker, useParams } from "@tanstack/react-router";

import { ContextHelpPortal } from "@/components/layout/ContextHelpPortal";

import {
  ActionMenu,
  Badge,
  Button,
  ConfirmDialog,
  Modal,
  PageHeader,
  Skeleton,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch, permissionBranchScope } from "@/features/auth/branchPermissions";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { AddItemForm } from "./AddItemForm";
import { deleteIncomingDraftRow } from "./api";
import { InvoiceImportForm } from "./InvoiceImportForm";
import { pharmacyCalendarDate } from "./calendar";
import { statusLabel, statusTone } from "./labels";
import { NewIncomingForm } from "./NewIncomingForm";
import {
  useAcceptIncoming,
  useDeleteIncomingItem,
  useIncomingDocQuery,
  useIncomingDraftRowsQuery,
  useRejectIncoming,
} from "./queries";
import { type IncomingDraftRow, type IncomingItem } from "./types";

export function IncomingDetailPage(): JSX.Element {
  const { user } = useAuth();
  const { id } = useParams({ from: "/incoming/$id" });
  const incoming = useIncomingDocQuery(id);
  const accept = useAcceptIncoming();
  const reject = useRejectIncoming();
  const deleteItem = useDeleteIncomingItem();
  const [adding, setAdding] = useState(false);
  const [importingInvoice, setImportingInvoice] = useState(false);
  const [invoiceImportBusy, setInvoiceImportBusy] = useState(false);
  const [activeDraft, setActiveDraft] = useState<IncomingDraftRow | undefined>();
  const [deletingDraft, setDeletingDraft] = useState<IncomingDraftRow | null>(null);
  const [draftDeletePending, setDraftDeletePending] = useState(false);
  const [draftDeleteError, setDraftDeleteError] = useState<string | null>(null);
  const [editingDocument, setEditingDocument] = useState(false);
  const [editingItem, setEditingItem] = useState<IncomingItem | null>(null);
  const [formDirty, setFormDirty] = useState(false);
  const [discardFormOpen, setDiscardFormOpen] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [docAction, setDocAction] = useState<"accept" | "reject" | null>(null);
  const [checkingAccept, setCheckingAccept] = useState(false);
  const [pendingDeleteItemId, setPendingDeleteItemId] = useState<string | null>(null);
  const [deleteItemError, setDeleteItemError] = useState<string | null>(null);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");
  const doc = incoming.data;
  const drafts = useIncomingDraftRowsQuery(id, doc?.status === "draft");
  const navigation = useBlocker({
    shouldBlockFn: () => formDirty,
    enableBeforeUnload: formDirty,
    withResolver: true,
  });

  const summary = useMemo(() => summarizeItems(doc?.items ?? []), [doc?.items]);

  if (incoming.isLoading) return <IncomingDetailSkeleton />;

  if (!doc) {
    return (
      <div className="space-y-5">
        <PageHeader
          title="Приход"
          description="Не удалось открыть документ. Уже введённые данные в других документах не изменены."
          actions={<BackToIncoming />}
        />
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(incoming.error, "Не удалось загрузить документ")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={incoming.isFetching}
            onClick={() => void incoming.refetch()}
          >
            Повторить
          </Button>
        </div>
      </div>
    );
  }

  const isDraft = doc.status === "draft";
  const canEdit = hasPermissionForBranch(user, "incoming.create", doc.branch_id);
  const canFinalize = hasPermissionForBranch(user, "incoming.finalize", doc.branch_id);
  const editableBranchScope = permissionBranchScope(user, "incoming.create");
  const title = doc.document_number ? `Приёмка № ${doc.document_number}` : "Приёмка без номера";
  const branchName = doc.branch_name ?? `Точка ${doc.branch_id.slice(0, 8)}`;
  const supplierName = doc.supplier_name ?? `Поставщик ${doc.supplier_id.slice(0, 8)}`;
  const pendingDeleteItem = doc.items.find((item) => item.id === pendingDeleteItemId) ?? null;
  const draftsBlockAccept =
    drafts.isPending ||
    drafts.isFetching ||
    drafts.isError ||
    !drafts.data ||
    drafts.data.length > 0;

  const onAccept = async () => {
    if (!canFinalize || accept.isPending || checkingAccept || draftsBlockAccept) return;
    setCheckingAccept(true);
    setTopError(null);
    setActionMessage(null);
    try {
      const currentDrafts = await drafts.refetch();
      if (currentDrafts.isError || !currentDrafts.data || currentDrafts.data.length > 0) {
        setTopError(
          "Сначала завершите или удалите незавершённые позиции. Проверка черновиков должна завершиться успешно.",
        );
        return;
      }
      await accept.mutateAsync(doc.id);
      setDocAction(null);
      setActionMessage("Товары приняты на склад. Документ теперь доступен только для просмотра.");
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось принять приход"));
    } finally {
      setCheckingAccept(false);
    }
  };

  const onReject = async () => {
    if (!canFinalize || reject.isPending) return;
    setTopError(null);
    setActionMessage(null);
    try {
      await reject.mutateAsync(doc.id);
      setDocAction(null);
      setActionMessage("Документ отклонён. Остатки на складе не изменились.");
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось отклонить приход"));
    }
  };

  const onDeleteItem = async () => {
    if (!pendingDeleteItemId || !canEdit || deleteItem.isPending) return;
    setDeleteItemError(null);
    try {
      await deleteItem.mutateAsync({ documentId: doc.id, itemId: pendingDeleteItemId });
      setPendingDeleteItemId(null);
    } catch (error) {
      setDeleteItemError(describeApiError(error, "Не удалось удалить позицию"));
    }
  };

  const closeItemForm = () => {
    setAdding(false);
    setActiveDraft(undefined);
    setEditingItem(null);
    setFormDirty(false);
    setDiscardFormOpen(false);
  };

  const closeDocumentForm = () => {
    setEditingDocument(false);
    setFormDirty(false);
    setDiscardFormOpen(false);
  };

  const requestCloseForm = () => {
    if (formDirty) {
      setDiscardFormOpen(true);
      return;
    }
    if (editingDocument) closeDocumentForm();
    else closeItemForm();
  };

  return (
    <div className="space-y-5">
      <ContextHelpPortal pathname="/incoming/detail" />
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
        <BackToIncoming />
        <Badge tone={statusTone[doc.status]}>{statusLabel[doc.status]}</Badge>
      </div>

      <PageHeader
        title={title}
        description={`${supplierName} · ${branchName}`}
        meta={formatCalendarDate(doc.document_date)}
        actions={
          canEdit && isDraft ? (
            <Button variant="secondary" onClick={() => setEditingDocument(true)}>
              Изменить реквизиты
            </Button>
          ) : undefined
        }
      />

      {incoming.isError && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm"
        >
          <p>
            Не удалось обновить документ. Показана последняя загруженная версия; ввод в открытой
            форме сохранён.
          </p>
          <Button
            variant="secondary"
            size="sm"
            isLoading={incoming.isFetching}
            onClick={() => void incoming.refetch()}
          >
            Повторить обновление
          </Button>
        </div>
      )}

      <section
        aria-label="Сводка прихода"
        className="overflow-hidden rounded-lg border border-border bg-surface"
      >
        <dl className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
          <SummaryField label="Позиций" value={doc.items.length.toLocaleString("ru-RU")} />
          <SummaryField label="Количество" value={formatQuantity(summary.quantity)} />
          <SummaryField
            label="Закупочная сумма"
            value={formatMoney(doc.total_amount, doc.currency)}
            emphasis
          />
          <SummaryField
            label="Потенциал продаж"
            value={formatMoney(summary.saleTotal, doc.currency)}
            note={
              summary.margin === null
                ? "Себестоимость скрыта"
                : summary.margin >= 0
                  ? `Наценка ${formatMoney(summary.margin, doc.currency)}`
                  : `Убыток ${formatMoney(Math.abs(summary.margin), doc.currency)}`
            }
            tone={summary.margin !== null && summary.margin < 0 ? "danger" : "default"}
          />
        </dl>
        {(doc.notes || summary.expiredCount > 0 || summary.lossItemCount > 0) && (
          <div className="grid gap-3 border-t border-border px-4 py-3 sm:grid-cols-2 sm:px-5">
            {doc.notes && (
              <div className="min-w-0">
                <p className="text-xs font-medium text-foreground-muted">Комментарий</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground-secondary">
                  {doc.notes}
                </p>
              </div>
            )}
            {(summary.expiredCount > 0 || summary.lossItemCount > 0) && (
              <div
                role="status"
                className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground"
              >
                {summary.expiredCount > 0 && (
                  <p>{summary.expiredCount} поз. с истёкшим сроком годности</p>
                )}
                {summary.lossItemCount > 0 && (
                  <p>{summary.lossItemCount} поз. с ценой продажи ниже закупочной</p>
                )}
              </div>
            )}
          </div>
        )}
      </section>

      {topError && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
        >
          {topError}
        </div>
      )}
      {actionMessage && (
        <div
          role="status"
          className="rounded-lg border border-success/30 bg-success-subtle px-4 py-3 text-sm text-success-foreground"
        >
          {actionMessage}
        </div>
      )}

      {isDraft && (
        <section
          aria-label="Незавершённые позиции"
          className="space-y-3 rounded-lg border border-border bg-surface p-4"
        >
          <h2 className="font-semibold">Незавершённые позиции</h2>
          <p className="text-sm text-foreground-muted">
            Сохранённый ввод можно продолжить на другом устройстве. В суммы и остатки он ещё не
            включён.
          </p>
          {drafts.isError ? (
            <div role="alert" className="space-y-2 text-sm text-danger">
              <p>Не удалось проверить черновые позиции. Принятие прихода временно недоступно.</p>
              <Button variant="secondary" size="sm" onClick={() => void drafts.refetch()}>
                Повторить проверку
              </Button>
            </div>
          ) : drafts.isPending ? (
            <p role="status">Загрузка черновых позиций…</p>
          ) : drafts.data?.length === 0 ? (
            <p className="text-sm text-foreground-muted">Незавершённых позиций нет</p>
          ) : (
            drafts.data?.map((row) => (
              <article
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3"
              >
                <div className="min-w-0">
                  <h3 className="break-words text-sm font-medium">
                    {row.catalog_name || row.fields.source_name || "Товар ещё не выбран"}
                  </h3>
                  {!row.catalog_name && row.fields.source_name && (
                    <p className="text-xs text-foreground-muted">
                      Из накладной · товар не сопоставлен
                    </p>
                  )}
                  <p className="break-words text-sm text-foreground-muted">
                    Количество: {row.fields.qty || "не указано"}
                  </p>
                </div>
                {canEdit && (
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      className="min-h-11"
                      onClick={() => {
                        setActiveDraft(row);
                        setAdding(true);
                      }}
                    >
                      Продолжить
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11"
                      onClick={() => {
                        setDeletingDraft(row);
                        setDraftDeleteError(null);
                      }}
                    >
                      Удалить
                    </Button>
                  </div>
                )}
              </article>
            ))
          )}
        </section>
      )}

      <section aria-labelledby="incoming-items-heading" className="space-y-3">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="incoming-items-heading" className="text-base font-semibold text-foreground">
              Позиции документа
            </h2>
            <p className="mt-0.5 text-xs text-foreground-muted">
              Партии появятся на складе только после принятия прихода.
            </p>
          </div>
          {canEdit && isDraft && (
            <div className="flex flex-wrap gap-2">
              <Button className="min-h-11" variant="secondary" onClick={() => setImportingInvoice(true)}>
                Загрузить накладную
              </Button>
              <Button className="min-h-11" onClick={() => setAdding(true)}>
                Добавить позицию
              </Button>
            </div>
          )}
        </div>

        {doc.items.length === 0 ? (
          <TableEmpty title="Документ пока пуст">
            Добавьте товары, проверьте цены и сроки годности перед принятием.
          </TableEmpty>
        ) : isDesktopLayout ? (
          <div>
            <Table className="min-w-[860px] table-fixed">
              <THead>
                <TR>
                  <TH className="w-[27%]">Товар</TH>
                  <TH className="w-[18%]">Партия и срок</TH>
                  <TH className="w-[11%] text-right">Кол-во</TH>
                  <TH className="w-[13%] text-right">Закупка</TH>
                  <TH className="w-[13%] text-right">Продажа</TH>
                  <TH className="w-[13%] text-right">Сумма</TH>
                  {canEdit && isDraft && (
                    <TH className="w-[5%] text-right">
                      <span className="sr-only">Действия</span>
                    </TH>
                  )}
                </TR>
              </THead>
              <TBody>
                {doc.items.map((item) => (
                  <IncomingItemRow
                    key={item.id}
                    item={item}
                    canEdit={canEdit && isDraft}
                    onEdit={() => setEditingItem(item)}
                    onDelete={() => {
                      setDeleteItemError(null);
                      setPendingDeleteItemId(item.id);
                    }}
                  />
                ))}
              </TBody>
            </Table>
          </div>
        ) : (
          <div className="divide-y divide-border rounded-lg border border-border bg-surface">
            {doc.items.map((item) => (
              <IncomingItemCard
                key={item.id}
                item={item}
                canEdit={canEdit && isDraft}
                onEdit={() => setEditingItem(item)}
                onDelete={() => {
                  setDeleteItemError(null);
                  setPendingDeleteItemId(item.id);
                }}
              />
            ))}
          </div>
        )}
      </section>

      {canFinalize && isDraft && (
        <div className="sticky bottom-2 z-sticky flex flex-col gap-3 rounded-lg border border-border bg-surface-raised px-3 py-3 shadow-lg sm:static sm:flex-row sm:items-center sm:justify-between sm:px-4 sm:shadow-none">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">Черновик не меняет остатки</p>
            <p className="text-xs text-foreground-muted">
              Перед принятием проверьте {doc.items.length} поз. на сумму{" "}
              {formatMoney(doc.total_amount, doc.currency)}.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:flex sm:shrink-0">
            <Button variant="secondary" onClick={() => setDocAction("reject")}>
              Отклонить
            </Button>
            <Button
              disabled={doc.items.length === 0 || draftsBlockAccept}
              title={
                draftsBlockAccept
                  ? "Сначала проверьте и завершите незавершённые позиции"
                  : doc.items.length === 0
                    ? "Сначала добавьте хотя бы одну позицию"
                    : undefined
              }
              onClick={() => setDocAction("accept")}
            >
              Принять на склад
            </Button>
          </div>
        </div>
      )}

      {canEdit && (
        <Modal
          open={adding || editingItem !== null}
          onClose={requestCloseForm}
          title={editingItem ? "Изменить позицию" : "Добавить позицию"}
          className="sm:max-w-2xl"
        >
          <AddItemForm
            key={editingItem?.id ?? activeDraft?.id ?? "new"}
            documentId={doc.id}
            item={editingItem ?? undefined}
            draftRow={activeDraft}
            onClose={closeItemForm}
            onCancel={requestCloseForm}
            onDirtyChange={setFormDirty}
          />
        </Modal>
      )}

      {canEdit && importingInvoice && (
        <Modal
          open
          onClose={() => {
            if (!invoiceImportBusy) setImportingInvoice(false);
          }}
          title="Загрузить накладную"
          className="sm:max-w-4xl"
        >
          <InvoiceImportForm
            documentId={doc.id}
            onClose={() => setImportingInvoice(false)}
            onImportingChange={setInvoiceImportBusy}
            onImported={async (result) => {
              await drafts.refetch();
              setImportingInvoice(false);
              setActionMessage(
                result.imported_count > 0
                  ? `${result.imported_count} строк добавлено в черновик. Сопоставьте товары и проверьте данные.`
                  : "Эта накладная уже была загружена. Дубли не созданы.",
              );
            }}
          />
        </Modal>
      )}

      {canEdit && (
        <Modal
          open={editingDocument}
          onClose={requestCloseForm}
          title="Реквизиты приёмки"
          className="sm:max-w-2xl"
        >
          <NewIncomingForm
            document={doc}
            allowedBranchIds={editableBranchScope}
            onClose={closeDocumentForm}
            onCancel={requestCloseForm}
            onDirtyChange={setFormDirty}
          />
        </Modal>
      )}

      {canFinalize && (
        <ConfirmDialog
          open={docAction === "accept"}
          title="Принять товары на склад"
          message={
            <>
              <span className="block">
                Будет создано партий: {doc.items.length}. Количество:{" "}
                {formatQuantity(summary.quantity)}. Закупочная сумма:{" "}
                {formatMoney(doc.total_amount, doc.currency)}.
              </span>
              <span className="mt-2 block font-medium">
                После принятия документ и его позиции нельзя изменить.
              </span>
              {topError && <span className="mt-2 block text-danger">{topError}</span>}
            </>
          }
          confirmLabel="Принять на склад"
          isLoading={accept.isPending || checkingAccept}
          onConfirm={() => void onAccept()}
          onCancel={() => {
            setDocAction(null);
            setTopError(null);
          }}
        />
      )}

      {canFinalize && (
        <ConfirmDialog
          open={docAction === "reject"}
          title="Отклонить приход"
          message={
            <>
              Документ останется в истории со статусом отклонённого и не изменит остатки.
              {topError && <span className="mt-2 block text-danger">{topError}</span>}
            </>
          }
          confirmLabel="Отклонить"
          variant="danger"
          isLoading={reject.isPending}
          onConfirm={() => void onReject()}
          onCancel={() => {
            setDocAction(null);
            setTopError(null);
          }}
        />
      )}

      <ConfirmDialog
        open={discardFormOpen}
        title="Закрыть без сохранения?"
        message="Несохранённые изменения в форме будут потеряны. Уже сохранённый серверный черновик останется доступен. Отправленная операция может завершиться на сервере."
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onConfirm={editingDocument ? closeDocumentForm : closeItemForm}
        onCancel={() => setDiscardFormOpen(false)}
      />

      {navigation.status === "blocked" &&
        createPortal(
          <ConfirmDialog
            open
            title="Уйти со страницы?"
            message="Несохранённые изменения могут быть потеряны. Подтверждённый сервером черновик останется доступен."
            confirmLabel="Уйти без сохранения"
            variant="danger"
            onConfirm={() => navigation.proceed?.()}
            onCancel={() => navigation.reset?.()}
          />,
          document.body,
        )}

      <ConfirmDialog
        open={deletingDraft !== null}
        title="Удалить незавершённую позицию?"
        message={
          <>
            <span>Сохранённый ввод этой позиции будет удалён.</span>
            {draftDeleteError && (
              <p role="alert" className="mt-2 text-danger">
                {draftDeleteError}
              </p>
            )}
          </>
        }
        confirmLabel="Удалить"
        variant="danger"
        isLoading={draftDeletePending}
        onCancel={() => {
          if (!draftDeletePending) setDeletingDraft(null);
        }}
        onConfirm={() => {
          if (!deletingDraft || draftDeletePending) return;
          setDraftDeletePending(true);
          void deleteIncomingDraftRow(doc.id, deletingDraft.id, deletingDraft.revision)
            .then(async () => {
              setDeletingDraft(null);
              await drafts.refetch();
            })
            .catch(() => {
              setDraftDeleteError(
                "Не удалось удалить черновик. Возможно, он изменён на другом устройстве. Обновите список и повторите.",
              );
              void drafts.refetch();
            })
            .finally(() => setDraftDeletePending(false));
        }}
      />

      {canEdit && (
        <ConfirmDialog
          open={pendingDeleteItem !== null}
          title="Удалить позицию"
          message={
            <>
              «{productName(pendingDeleteItem)}» будет удалена из черновика прихода.
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

function IncomingItemRow({
  item,
  canEdit,
  onEdit,
  onDelete,
}: {
  item: IncomingItem;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => void;
}): JSX.Element {
  const lineTotal =
    item.purchase_price === null ? null : Number(item.qty) * Number(item.purchase_price);
  const isExpired = item.expires_at <= pharmacyCalendarDate();
  const isLoss =
    item.purchase_price !== null && Number(item.sale_price) < Number(item.purchase_price);

  return (
    <TR>
      <TD>
        <p className="truncate font-medium text-foreground">{productName(item)}</p>
        {productDetails(item) && (
          <p className="mt-0.5 truncate text-xs text-foreground-muted">{productDetails(item)}</p>
        )}
      </TD>
      <TD>
        <p className="truncate font-mono text-xs">{item.batch_number ?? "Без номера"}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className={cn("text-xs", isExpired && "text-danger")}>
            до {formatCalendarDate(item.expires_at)}
          </span>
          {isExpired && <Badge tone="danger">просрочена</Badge>}
          {item.created_batch_id && <Badge tone="success">на складе</Badge>}
        </div>
      </TD>
      <TD className="text-right font-mono tabular-nums">{formatQuantity(item.qty)}</TD>
      <TD className="text-right font-mono tabular-nums">
        {formatMoney(item.purchase_price, item.currency)}
      </TD>
      <TD className={cn("text-right font-mono tabular-nums", isLoss && "text-danger")}>
        {formatMoney(item.sale_price, item.currency)}
      </TD>
      <TD className="text-right font-mono font-semibold tabular-nums">
        {formatMoney(lineTotal, item.currency)}
      </TD>
      {canEdit && (
        <TD className="text-right">
          <ActionMenu
            label={`Действия с позицией «${productName(item)}»`}
            items={[
              { label: "Изменить", onSelect: onEdit },
              { label: "Удалить", onSelect: onDelete, tone: "danger" },
            ]}
          />
        </TD>
      )}
    </TR>
  );
}

function IncomingItemCard({
  item,
  canEdit,
  onEdit,
  onDelete,
}: {
  item: IncomingItem;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => void;
}): JSX.Element {
  const isExpired = item.expires_at <= pharmacyCalendarDate();
  const lineTotal =
    item.purchase_price === null ? null : Number(item.qty) * Number(item.purchase_price);
  return (
    <article aria-label={productName(item)} className="space-y-3 px-3 py-4">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-words text-sm font-semibold text-foreground">{productName(item)}</h3>
          {productDetails(item) && (
            <p className="mt-0.5 text-xs text-foreground-muted">{productDetails(item)}</p>
          )}
        </div>
        {isExpired ? (
          <Badge tone="danger">просрочена</Badge>
        ) : item.created_batch_id ? (
          <Badge tone="success">на складе</Badge>
        ) : null}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <CompactField label="Партия" value={item.batch_number ?? "Без номера"} mono />
        <CompactField label="Срок" value={formatCalendarDate(item.expires_at)} />
        <CompactField label="Количество" value={formatQuantity(item.qty)} mono />
        <CompactField label="Закупочная сумма" value={formatMoney(lineTotal, item.currency)} mono />
        <CompactField
          label="Закупка / ед."
          value={formatMoney(item.purchase_price, item.currency)}
          mono
        />
        <CompactField
          label="Продажа / ед."
          value={formatMoney(item.sale_price, item.currency)}
          mono
        />
      </dl>
      {canEdit && (
        <div className="grid grid-cols-2 gap-2 border-t border-border pt-3">
          <Button className="min-h-11" variant="secondary" onClick={onEdit}>
            Изменить
          </Button>
          <Button className="min-h-11" variant="ghost" onClick={onDelete}>
            Удалить
          </Button>
        </div>
      )}
    </article>
  );
}

function BackToIncoming(): JSX.Element {
  return (
    <Link to="/incoming">
      <Button variant="ghost" size="sm">
        ← Все приходы
      </Button>
    </Link>
  );
}

function SummaryField({
  label,
  value,
  note,
  emphasis = false,
  tone = "default",
}: {
  label: string;
  value: string;
  note?: string;
  emphasis?: boolean;
  tone?: "default" | "danger";
}): JSX.Element {
  return (
    <div className="min-w-0 bg-surface px-4 py-4 sm:px-5">
      <dt className="text-xs font-medium text-foreground-muted">{label}</dt>
      <dd
        className={cn(
          "mt-1 break-words font-mono font-semibold tabular-nums text-foreground",
          emphasis ? "text-lg" : "text-base",
          tone === "danger" && "text-danger",
        )}
      >
        {value}
      </dd>
      {note && <dd className="mt-0.5 text-xs text-foreground-muted">{note}</dd>}
    </div>
  );
}

function CompactField({
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
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className={cn("mt-0.5 break-words text-foreground", mono && "font-mono tabular-nums")}>
        {value}
      </dd>
    </div>
  );
}

function IncomingDetailSkeleton(): JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Загрузка прихода">
      <div className="flex items-center justify-between">
        <Skeleton className="h-9 w-32" />
        <Skeleton className="h-6 w-24" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-8 w-64 max-w-full" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="space-y-2 bg-surface px-4 py-4">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-6 w-28 max-w-full" />
          </div>
        ))}
      </div>
      <SkeletonRows rows={5} />
    </div>
  );
}

function summarizeItems(items: readonly IncomingItem[]): {
  quantity: number;
  saleTotal: number;
  margin: number | null;
  expiredCount: number;
  lossItemCount: number;
} {
  let quantity = 0;
  let purchaseTotal = 0;
  let hasHiddenCost = false;
  let saleTotal = 0;
  let expiredCount = 0;
  let lossItemCount = 0;
  const today = pharmacyCalendarDate();
  for (const item of items) {
    const qty = Number(item.qty);
    const purchasePrice = item.purchase_price === null ? null : Number(item.purchase_price);
    const salePrice = Number(item.sale_price);
    quantity += qty;
    if (purchasePrice === null) {
      hasHiddenCost = true;
    } else {
      purchaseTotal += qty * purchasePrice;
    }
    saleTotal += qty * salePrice;
    if (item.expires_at <= today) expiredCount += 1;
    if (purchasePrice !== null && salePrice < purchasePrice) lossItemCount += 1;
  }
  return {
    quantity,
    saleTotal,
    margin: hasHiddenCost ? null : saleTotal - purchaseTotal,
    expiredCount,
    lossItemCount,
  };
}

function productName(item: IncomingItem | null): string {
  if (!item) return "Позиция";
  return item.catalog_name?.trim() || `Товар ${item.catalog_id.slice(0, 8)}`;
}

function productDetails(item: IncomingItem): string {
  return [item.catalog_form, item.catalog_dosage, item.catalog_pack_size]
    .filter((value): value is string => Boolean(value))
    .join(" · ");
}

const CALENDAR_DATE_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  timeZone: "UTC",
});

const QUANTITY_FORMATTER = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

const MONEY_FORMATTER = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCalendarDate(value: string): string {
  return CALENDAR_DATE_FORMATTER.format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

function formatQuantity(value: string | number): string {
  return QUANTITY_FORMATTER.format(Number(value));
}

function formatMoney(value: string | number | null, currency: string): string {
  if (value === null) return "Скрыто";
  return `${MONEY_FORMATTER.format(Number(value))} ${currency}`;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(query).matches === true,
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mediaQuery = window.matchMedia(query);
    const update = () => setMatches(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, [query]);

  return matches;
}
