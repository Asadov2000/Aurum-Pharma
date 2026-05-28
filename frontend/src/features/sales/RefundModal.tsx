import { useState } from "react";

import {
  Button,
  Input,
  Label,
  Modal,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { type SaleDetails } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";

import { useRefundSale } from "./queries";

interface LineState {
  selected: boolean;
  qty: string;
}

export function RefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleDetails;
  onClose: () => void;
  onRefunded: (returnSaleId: string) => void;
}): JSX.Element {
  const refund = useRefundSale();
  const [topError, setTopError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  // Default: every line selected at full quantity (the common "вернуть всё").
  const [lines, setLines] = useState<Record<string, LineState>>(() =>
    Object.fromEntries(
      sale.items.map((it) => [it.id, { selected: true, qty: it.qty }]),
    ),
  );

  const setLine = (id: string, patch: Partial<LineState>) =>
    setLines((prev) => ({ ...prev, [id]: { ...prev[id]!, ...patch } }));

  const onSubmit = async () => {
    const chosen = sale.items
      .filter((it) => lines[it.id]?.selected)
      .map((it) => ({ sale_item_id: it.id, qty: lines[it.id]!.qty }));

    if (chosen.length === 0) {
      setTopError("Выберите хотя бы одну позицию для возврата.");
      return;
    }
    for (const it of sale.items) {
      const l = lines[it.id];
      if (!l?.selected) continue;
      const q = Number(l.qty);
      if (!(q > 0)) {
        setTopError("Количество возврата должно быть больше 0.");
        return;
      }
      if (q > Number(it.qty)) {
        setTopError("Количество возврата больше, чем в чеке.");
        return;
      }
    }

    setTopError(null);
    try {
      const returnSale = await refund.mutateAsync({
        parentSaleId: sale.id,
        payload: {
          items: chosen,
          reason: reason.trim() || null,
          comment: comment.trim() || null,
        },
      });
      onRefunded(returnSale.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось оформить возврат."));
    }
  };

  return (
    <Modal open onClose={onClose} title={`Возврат по чеку № ${sale.receipt_number ?? "—"}`}>
      <div className="space-y-4">
        <Table>
          <THead>
            <TR>
              <TH className="w-10"></TH>
              <TH>Позиция</TH>
              <TH className="text-right">В чеке</TH>
              <TH className="text-right">Вернуть</TH>
            </TR>
          </THead>
          <TBody>
            {sale.items.map((it) => {
              const l = lines[it.id]!;
              return (
                <TR key={it.id}>
                  <TD>
                    <input
                      type="checkbox"
                      aria-label={`Вернуть позицию ${it.position}`}
                      checked={l.selected}
                      onChange={(e) => setLine(it.id, { selected: e.target.checked })}
                    />
                  </TD>
                  <TD className="font-mono text-xs">{it.catalog_id.slice(0, 8)}</TD>
                  <TD className="text-right font-mono">{it.qty}</TD>
                  <TD className="text-right">
                    <Input
                      type="text"
                      inputMode="decimal"
                      value={l.qty}
                      disabled={!l.selected}
                      onChange={(e) => setLine(it.id, { qty: e.target.value })}
                      className="w-20 text-right"
                    />
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="refund_reason">Причина</Label>
            <Input
              id="refund_reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="брак, ошибка кассира…"
            />
          </div>
          <div>
            <Label htmlFor="refund_comment">Комментарий</Label>
            <Textarea
              id="refund_comment"
              rows={1}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
        </div>

        {topError && <p className="text-sm text-danger">{topError}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={() => void onSubmit()} isLoading={refund.isPending}>
            Оформить возврат
          </Button>
        </div>
      </div>
    </Modal>
  );
}
