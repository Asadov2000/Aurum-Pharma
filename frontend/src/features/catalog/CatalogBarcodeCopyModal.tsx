import { useState } from "react";

import { Button, Modal } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { barcodeLabel } from "./labels";
import { useCatalogItemQuery } from "./queries";

export function CatalogBarcodeCopyModal({
  itemId,
  onClose,
}: {
  itemId: string;
  onClose: () => void;
}): JSX.Element {
  const query = useCatalogItemQuery(itemId);
  const [message, setMessage] = useState<string | null>(null);
  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setMessage("Штрихкод скопирован.");
    } catch {
      setMessage("Не удалось скопировать. Выделите код и скопируйте его вручную.");
    }
  };
  return (
    <Modal open onClose={onClose} title="Скопировать штрихкод">
      <div className="space-y-3">
        <p className="text-sm text-foreground-secondary">
          Выберите нужный код. Если у товара несколько штрихкодов, проверьте упаковку.
        </p>
        {query.isPending ? (
          <p role="status">Загрузка штрихкодов…</p>
        ) : query.isError ? (
          <>
            <p role="alert">{describeApiError(query.error, "Не удалось загрузить штрихкоды.")}</p>
            <Button variant="secondary" onClick={() => void query.refetch()}>
              Повторить
            </Button>
          </>
        ) : query.data?.barcodes.length ? (
          <ul className="space-y-2" aria-label="Штрихкоды товара">
            {query.data.barcodes.map((barcode) => (
              <li
                key={barcode.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3"
              >
                <span>
                  <span className="block font-mono text-sm">{barcode.code}</span>
                  <span className="text-xs text-foreground-muted">
                    {barcodeLabel[barcode.code_type]}
                  </span>
                </span>
                <Button variant="secondary" size="sm" onClick={() => void copy(barcode.code)}>
                  Скопировать
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p>У этого товара пока нет штрихкодов.</p>
        )}
        {message ? (
          <p role="status" className="text-sm text-foreground-secondary">
            {message}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
