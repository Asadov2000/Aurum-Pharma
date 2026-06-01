import { useState } from "react";

import { Button, Select } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { getReceiptPdf } from "./api";
import { paymentMethodLabel } from "./labels";
import { useReceiptQuery } from "./queries";
import {
  RECEIPT_WIDTHS,
  fmtQty,
  loadReceiptWidth,
  money,
  printConfigFor,
  printCss,
  receiptWidthLabel,
  saveReceiptWidth,
} from "./receiptFormat";
import { type PaymentMethod, type ReceiptData, type ReceiptWidth } from "./types";

/**
 * Print/preview overlay for a single receipt. Renders the receipt as "paper"
 * (white/black, monospace), lets the cashier pick the ribbon/page width
 * (persisted per device per register), prints via window.print() with an
 * injected @page stylesheet, and can download the server-rendered PDF.
 */
export function ReceiptPrintModal({
  saleId,
  registerId,
  onClose,
}: {
  saleId: string;
  registerId: string;
  onClose: () => void;
}): JSX.Element {
  const { data, isLoading, error } = useReceiptQuery(saleId);
  const [width, setWidth] = useState<ReceiptWidth>(() => loadReceiptWidth(registerId));
  const [downloading, setDownloading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const cfg = printConfigFor(width);

  const onWidthChange = (w: ReceiptWidth) => {
    setWidth(w);
    saveReceiptWidth(registerId, w);
  };

  const onDownload = async () => {
    setActionError(null);
    setDownloading(true);
    try {
      const blob = await getReceiptPdf(saleId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `receipt-${data?.receipt_number ?? saleId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError(describeApiError(err, "Не удалось скачать PDF"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div
      className="bg-overlay fixed inset-0 z-modal flex flex-col items-center overflow-y-auto p-4"
      role="presentation"
      onClick={onClose}
    >
      {/* Print stylesheet — kept in sync with the chosen width. */}
      <style>{printCss(width)}</style>

      <div
        className="receipt-no-print mb-3 flex w-full max-w-md flex-wrap items-center gap-2 rounded-xl border border-border bg-surface-raised p-3 shadow-lg"
        role="dialog"
        aria-modal="true"
        aria-label="Печать чека"
        onClick={(e) => e.stopPropagation()}
      >
        <Select
          aria-label="Ширина чека"
          value={width}
          onChange={(e) => onWidthChange(e.target.value as ReceiptWidth)}
          className="h-9 w-40"
        >
          {RECEIPT_WIDTHS.map((w) => (
            <option key={w} value={w}>
              {receiptWidthLabel[w]}
            </option>
          ))}
        </Select>
        <Button onClick={() => window.print()} disabled={!data}>
          Печать чека
        </Button>
        <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading} disabled={!data}>
          Скачать PDF
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
        {actionError && <p className="w-full text-sm text-danger">{actionError}</p>}
      </div>

      <div onClick={(e) => e.stopPropagation()}>
        {error ? (
          <p className="rounded-md bg-surface p-4 text-sm text-danger">
            {describeApiError(error, "Не удалось загрузить чек")}
          </p>
        ) : isLoading || !data ? (
          <p className="rounded-md bg-surface p-4 text-sm text-foreground-muted">Загрузка…</p>
        ) : (
          <ReceiptDocument data={data} contentWidth={cfg.contentWidth} />
        )}
      </div>
    </div>
  );
}

/** The receipt itself — white paper, black ink, monospace (thermal look). */
function ReceiptDocument({
  data,
  contentWidth,
}: {
  data: ReceiptData;
  contentWidth: string;
}): JSX.Element {
  const dt = data.datetime ? new Date(data.datetime).toLocaleString("ru-RU") : "—";
  return (
    <div
      className="receipt-print mx-auto bg-white text-black shadow-lg"
      style={{ width: contentWidth }}
    >
      <div className="px-3 py-4 font-mono text-[12px] leading-snug">
        <div className="text-center">
          <p className="text-base font-bold uppercase">{data.pharmacy_name || "—"}</p>
          {data.branch_name && <p>{data.branch_name}</p>}
          {data.branch_address && <p className="text-[11px]">{data.branch_address}</p>}
          {data.branch_license && <p className="text-[11px]">Лицензия: {data.branch_license}</p>}
        </div>

        <Divider />
        <p className="text-center font-bold">{data.is_refund ? "ВОЗВРАТ" : "КАССОВЫЙ ЧЕК"}</p>
        <p>Чек № {data.receipt_number ?? "—"}</p>
        <p>Дата: {dt}</p>
        {data.cashier_name && <p>Кассир: {data.cashier_name}</p>}
        <Divider />

        {data.items.map((it) => (
          <div key={it.position} className="mb-1">
            <p className="break-words">
              {it.position}. {it.name}
            </p>
            <div className="flex justify-between tabular-nums">
              <span>
                {fmtQty(it.qty)} × {money(it.unit_price)}
              </span>
              <span>{money(it.total_price)}</span>
            </div>
          </div>
        ))}

        <Divider />
        {Number(data.discount_total) > 0 && (
          <Row label="Скидка" value={`${money(data.discount_total)} ${data.currency}`} />
        )}
        <div className="flex justify-between text-sm font-bold tabular-nums">
          <span>ИТОГО</span>
          <span>
            {money(data.total)} {data.currency}
          </span>
        </div>

        <Divider />
        {data.payments.map((p, i) => (
          <Row
            key={i}
            label={paymentMethodLabel[p.method as PaymentMethod] ?? p.method}
            value={`${money(p.amount)} ${data.currency}`}
          />
        ))}
        <Row label="Принято" value={`${money(data.paid_total)} ${data.currency}`} />
        <Row label="Сдача" value={`${money(data.change)} ${data.currency}`} />

        <Divider />
        <p className="text-center text-[11px]">Спасибо за покупку!</p>
      </div>
    </div>
  );
}

function Divider(): JSX.Element {
  return <div className="my-2 border-t border-dashed border-black/40" aria-hidden="true" />;
}

function Row({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex justify-between tabular-nums">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}
