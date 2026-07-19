import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Badge, Button, Card, CardContent, Input, Label, Modal, Textarea } from "@/components/ui";
import { downloadBlob } from "@/lib/download";
import { describeApiError } from "@/lib/errorMessages";

import { getZReportXlsx } from "./api";
import { posKeys, useCloseShift, useCurrentShiftQuery, useOpenShift } from "./queries";
import { type PosMode } from "./usePosMode";

export function ShiftBar({
  registerId,
  mode = "keyboard",
}: {
  registerId: string;
  mode?: PosMode;
}): JSX.Element {
  const shiftQuery = useCurrentShiftQuery(registerId);
  const queryClient = useQueryClient();
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
      const refreshed = await shiftQuery.refetch();
      if (refreshed.data) {
        queryClient.setQueryData(posKeys.shift(registerId), refreshed.data);
        return;
      }
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
      // Auto-download the Z-report XLSX. Best-effort: the shift is already
      // closed, so a download hiccup must not surface as a close failure —
      // the cashier can re-download from «Отчёты».
      try {
        const blob = await getZReportXlsx(shift.id);
        downloadBlob(blob, `z-report-${shift.id}.xlsx`);
      } catch {
        // ignore — re-downloadable from Отчёты
      }
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось закрыть смену"));
    }
  };

  // F9 toggles the shift in keyboard mode: open it (no shift) or pop the close
  // dialog. Ref keeps the latest closures so the listener binds once.
  const toggleRef = useRef<() => void>(() => {});
  toggleRef.current = () => {
    if (shift) setCloseOpen(true);
    else void onOpen();
  };
  useEffect(() => {
    if (mode !== "keyboard") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "F9") return;
      if (document.querySelector('[role="dialog"]')) return;
      e.preventDefault();
      toggleRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [mode]);

  if (shiftQuery.isLoading) {
    return <p className="text-sm text-foreground-muted">Загрузка состояния смены…</p>;
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
          <Button
            onClick={() => void onOpen()}
            isLoading={openMutation.isPending}
            title={mode === "keyboard" ? "Открыть смену (F9)" : undefined}
          >
            Открыть смену
          </Button>
          {topError && <p className="ml-2 text-sm text-danger">{topError}</p>}
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {/* Slim status strip — compact so it doesn't crowd the selling area. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-4 py-2.5 text-sm shadow-sm">
        <Badge tone="success">Смена открыта</Badge>
        <span className="text-foreground-muted">
          Открыта{" "}
          <span className="text-foreground-secondary">
            {new Date(shift.opened_at).toLocaleString("ru-RU")}
          </span>
        </span>
        <span className="text-foreground-muted">
          Начальная касса{" "}
          <span className="font-mono tabular-nums text-foreground-secondary">
            {Number(shift.opening_cash).toFixed(2)} {shift.currency}
          </span>
        </span>
        <Button
          variant="secondary"
          size="sm"
          className="ml-auto"
          onClick={() => setCloseOpen(true)}
          title={mode === "keyboard" ? "Закрыть смену (F9)" : undefined}
        >
          Закрыть смену
        </Button>
      </div>

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
            <p className="mt-1 text-xs text-foreground-muted">
              Сумма в кассе после пересчёта. Расхождение с ожидаемым появится в Z-отчёте.
            </p>
          </div>
          <div>
            <Label htmlFor="notes">Комментарий</Label>
            <Textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          {topError && <p className="text-sm text-danger">{topError}</p>}
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
