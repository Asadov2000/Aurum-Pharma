import { useCallback, useState } from "react";
import { createPortal } from "react-dom";
import { useBlocker } from "@tanstack/react-router";

import { ConfirmDialog, Modal } from "@/components/ui";
import { CatalogItemForm } from "./CatalogItemForm";
import { type CatalogItem } from "./types";

export function CatalogCreateDialog({
  open,
  onClose,
  initialName = "",
  onCreated,
  onDirtyChange,
  guardNavigation = true,
}: {
  open: boolean;
  onClose: () => void;
  initialName?: string;
  onCreated?: (item: CatalogItem) => void;
  onDirtyChange?: (dirty: boolean) => void;
  /** Incoming already owns a page-wide blocker and aggregates this form's dirty state. */
  guardNavigation?: boolean;
}) {
  const [dirty, setDirty] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const handleDirty = useCallback(
    (value: boolean) => {
      setDirty(value);
      onDirtyChange?.(value);
    },
    [onDirtyChange],
  );
  const close = () => {
    setDirty(false);
    onDirtyChange?.(false);
    setDiscardOpen(false);
    onClose();
  };
  const requestClose = () => {
    if (dirty) setDiscardOpen(true);
    else close();
  };
  return createPortal(
    <>
      <Modal open={open} onClose={requestClose} title="Добавить товар" className="max-w-2xl">
        <CatalogItemForm
          item={null}
          initialName={initialName}
          onCreated={onCreated}
          onClose={close}
          onCancel={requestClose}
          onDirtyChange={handleDirty}
        />
      </Modal>
      <ConfirmDialog
        open={discardOpen}
        title="Закрыть карточку товара?"
        message="Несохранённые поля карточки будут потеряны. Если запрос уже отправлен, создание товара может завершиться на сервере."
        confirmLabel="Закрыть карточку"
        variant="danger"
        onConfirm={close}
        onCancel={() => setDiscardOpen(false)}
      />
      {open && guardNavigation && <CatalogCreateNavigationGuard dirty={dirty} />}
    </>,
    document.body,
  );
}

function CatalogCreateNavigationGuard({ dirty }: { dirty: boolean }) {
  const navigation = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: dirty,
    withResolver: true,
  });
  return (
    <ConfirmDialog
      open={navigation.status === "blocked"}
      title="Уйти со страницы?"
      message="Несохранённые поля карточки будут потеряны. Отправленное создание товара может завершиться на сервере."
      confirmLabel="Уйти без сохранения"
      variant="danger"
      onConfirm={() => navigation.proceed?.()}
      onCancel={() => navigation.reset?.()}
    />
  );
}
