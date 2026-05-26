import { useState } from "react";

import { Badge, Button, Card, CardContent, Input, Label, Modal, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useCloseShift, useCurrentShiftQuery, useOpenShift } from "./queries";

export function ShiftBar({ registerId }: { registerId: string }): JSX.Element {
  const shiftQuery = useCurrentShiftQuery(registerId);
  const openMutation = useOpenShift();
  const closeMutation = useCloseShift();
  const [openingCash, setOpeningCash] = useState("0");
  const [closingCash, setClosingCash] = useState("");
  const [notes, setNotes] = useState("");
  const [closeOpen, setCloseOpen] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);

  const shift = shiftQuery.data;

  const onOpen = async () => {
    if (Number(openingCash) < 0) {
      setTopError("Сумма не может быть отрицательной");
      return;
    }
    setTopError(null);
    try {
      await openMutation.mutateAsync({ register_id: registerId, opening_cash: openingCash });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось открыть смену"));
    }
  };

  const onClose = async () => {
    if (!shift) return;
    if (closingCash === "" || Number(closingCash) < 0) {
      setTopError("Введите фактическую сумму наличных");
      return;
    }
    setTopError(null);
    try {
      await closeMutation.mutateAsync({
        shiftId: shift.id,
        registerId,
        payload: {
          closing_cash_actual: closingCash,
          notes: notes.trim() || null,
        },
      });
      // Stash the just-closed shift id so /reports can preload the Z-report.
      window.localStorage.setItem("pos:lastClosedShiftId", shift.id);
      setCloseOpen(false);
      setClosingCash("");
      setNotes("");
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось закрыть смену"));
    }
  };

  if (shiftQuery.isLoading) {
    return <p className="text-sm text-slate-500">Загрузка состояния смены…</p>;
  }

  if (!shift) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 py-4">
          <div>
            <Label htmlFor="opening_cash">Касса на начало смены</Label>
            <Input
              id="opening_cash"
              type="text"
              inputMode="decimal"
              value={openingCash}
              onChange={(e) => setOpeningCash(e.target.value)}
              className="w-40"
            />
          </div>
          <Button onClick={() => void onOpen()} isLoading={openMutation.isPending}>
            Открыть смену
          </Button>
          {topError && <p className="ml-2 text-sm text-red-600">{topError}</p>}
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-4">
          <Badge tone="success">Смена открыта</Badge>
          <div className="text-sm">
            <p className="text-xs text-slate-500">Открыта</p>
            <p>{new Date(shift.opened_at).toLocaleString("ru-RU")}</p>
          </div>
          <div className="text-sm">
            <p className="text-xs text-slate-500">Начальная касса</p>
            <p className="font-mono">
              {Number(shift.opening_cash).toFixed(2)} {shift.currency}
            </p>
          </div>
          <div className="ml-auto">
            <Button variant="secondary" onClick={() => setCloseOpen(true)}>
              Закрыть смену
            </Button>
          </div>
        </CardContent>
      </Card>

      <Modal open={closeOpen} onClose={() => setCloseOpen(false)} title="Закрытие смены">
        <div className="space-y-3">
          <div>
            <Label htmlFor="closing_cash">Фактическая касса</Label>
            <Input
              id="closing_cash"
              type="text"
              inputMode="decimal"
              value={closingCash}
              onChange={(e) => setClosingCash(e.target.value)}
              autoFocus
            />
            <p className="mt-1 text-xs text-slate-500">
              Сумма в кассе после пересчёта. Расхождение с ожидаемым появится в Z-отчёте.
            </p>
          </div>
          <div>
            <Label htmlFor="notes">Комментарий</Label>
            <Textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          {topError && <p className="text-sm text-red-600">{topError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCloseOpen(false)}>
              Отмена
            </Button>
            <Button onClick={() => void onClose()} isLoading={closeMutation.isPending}>
              Закрыть
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
