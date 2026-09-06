import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  Input,
  Label,
  Modal,
  TableEmpty,
  Textarea,
} from "@/components/ui";
import { downloadBlob } from "@/lib/download";
import { describeApiError } from "@/lib/errorMessages";

import { getZReportXlsx } from "./api";
import { posKeys, useCloseShift, useCurrentShiftQuery, useOpenShift } from "./queries";
import { type PosMode } from "./usePosMode";

export function ShiftBar({
  registerId,
  mode = "keyboard",
  canOpen = true,
  canClose = true,
  canExportReport = false,
  closeBlocked = false,
  online = true,
}: {
  registerId: string;
  mode?: PosMode;
  canOpen?: boolean;
  canClose?: boolean;
  canExportReport?: boolean;
  closeBlocked?: boolean;
  online?: boolean;
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
  const [openingCashError, setOpeningCashError] = useState<string | null>(null);
  const [closingCashError, setClosingCashError] = useState<string | null>(null);
  const openingCashRef = useRef<HTMLInputElement>(null);

  const shift = shiftQuery.data;
  const openedTime = shift ? new Date(shift.opened_at).toLocaleTimeString("ru-RU") : "";

  const onOpen = async () => {
    if (!canOpen || !online) return;
    const normalizedOpeningCash = normalizeCashAmount(openingCash);
    if (normalizedOpeningCash === null) {
      setOpeningCashError(CASH_AMOUNT_ERROR);
      openingCashRef.current?.focus();
      return;
    }
    setOpeningCashError(null);
    setTopError(null);
    try {
      await openMutation.mutateAsync({
        register_id: registerId,
        opening_cash: normalizedOpeningCash,
      });
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
    if (!shift || !canClose || closeBlocked || !online) return;
    const normalizedClosingCash = normalizeCashAmount(closingCash);
    if (normalizedClosingCash === null) {
      setClosingCashError(CASH_AMOUNT_ERROR);
      return;
    }
    setClosingCashError(null);
    setTopError(null);
    try {
      await closeMutation.mutateAsync({
        shiftId: shift.id,
        registerId,
        payload: {
          closing_cash_actual: normalizedClosingCash,
          notes: notes.trim() || null,
        },
      });
      await finishClosedShift(shift.id);
    } catch (err) {
      const refreshed = await shiftQuery.refetch();
      if (refreshed.isSuccess && !refreshed.data) {
        await finishClosedShift(shift.id);
        return;
      }
      setTopError(describeApiError(err, "Не удалось закрыть смену"));
    }
  };

  const finishClosedShift = async (shiftId: string) => {
    // Stash the just-closed shift id so /reports can preload the Z-report.
    try {
      window.localStorage.setItem("pos:lastClosedShiftId", shiftId);
    } catch {
      // Closing the shift must not depend on optional browser storage.
    }
    setCloseOpen(false);
    setClosingCash("");
    setClosingCashError(null);
    setNotes("");
    if (canExportReport) {
      try {
        const blob = await getZReportXlsx(shiftId);
        downloadBlob(blob, `z-report-${shiftId}.xlsx`);
      } catch {
        // The report remains available from «Отчёты».
      }
    }
  };

  // F9 never submits money: it focuses the opening amount or opens the close dialog.
  const toggleRef = useRef<() => void>(() => {});
  toggleRef.current = () => {
    if (shift) {
      if (canClose && !closeBlocked && online) setCloseOpen(true);
    } else if (canOpen) {
      openingCashRef.current?.focus();
      openingCashRef.current?.select();
    }
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

  if (shiftQuery.error) {
    return (
      <div
        className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
        role="alert"
      >
        <p>Не удалось проверить состояние смены. Продажа пока недоступна.</p>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="mt-2"
          onClick={() => void shiftQuery.refetch()}
        >
          Повторить
        </Button>
      </div>
    );
  }

  if (!shift) {
    if (!canOpen) {
      return (
        <TableEmpty title="Смена закрыта">Открытие смены недоступно для этого аккаунта.</TableEmpty>
      );
    }
    return (
      <Card>
        <CardContent className="space-y-4 py-5">
          <div>
            <h2 className="text-base font-semibold text-foreground">Открытие смены</h2>
            <p className="mt-1 text-sm text-foreground-muted">
              Укажите фактическую наличность в кассе.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1">
              <Label htmlFor="opening_cash">Наличные в кассе на начало смены</Label>
              <Input
                ref={openingCashRef}
                id="opening_cash"
                type="text"
                inputMode="decimal"
                value={openingCash}
                onChange={(e) => {
                  setOpeningCash(e.target.value);
                  if (openingCashError) setOpeningCashError(null);
                }}
                invalid={openingCashError !== null}
                aria-describedby={openingCashError ? "opening-cash-error" : undefined}
                aria-keyshortcuts={mode === "keyboard" ? "F9" : undefined}
                autoComplete="off"
                maxLength={15}
                className="w-full"
              />
              {openingCashError ? (
                <p id="opening-cash-error" className="mt-1 text-xs text-danger" role="alert">
                  {openingCashError}
                </p>
              ) : null}
            </div>
            <Button
              onClick={() => void onOpen()}
              isLoading={openMutation.isPending}
              disabled={!online}
              size={mode === "touch" ? "lg" : "md"}
              title={!online ? "Открытие смены доступно после восстановления связи" : undefined}
            >
              Открыть смену
            </Button>
          </div>
          {topError && (
            <p
              className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
              role="alert"
            >
              {topError}
            </p>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {/* Slim status strip — compact so it doesn't crowd the selling area. */}
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-surface px-4 py-2.5 text-sm xl:gap-x-2 xl:px-3 xl:py-2 xl:text-xs 2xl:flex-nowrap 2xl:gap-y-0">
        <Badge tone="success">Смена открыта</Badge>
        <span className="shrink-0 whitespace-nowrap text-foreground-muted" title={openedTime}>
          С {openedTime}
        </span>
        <span className="shrink-0 whitespace-nowrap text-foreground-muted">
          Касса{" "}
          <span className="font-mono tabular-nums text-foreground-secondary">
            {Number(shift.opening_cash).toFixed(2)} {shift.currency}
          </span>
        </span>
        {canClose && closeBlocked && (
          <span className="text-xs text-warning-foreground">
            Завершите или очистите текущий чек перед закрытием смены.
          </span>
        )}
        {canClose && (
          <Button
            variant="secondary"
            size="sm"
            className="ml-auto shrink-0"
            disabled={closeBlocked || !online}
            onClick={() => setCloseOpen(true)}
            title={
              !online
                ? "Закрытие смены доступно после восстановления связи"
                : closeBlocked
                  ? "Сначала завершите или очистите текущий чек"
                  : mode === "keyboard"
                    ? "Закрыть смену (F9)"
                    : undefined
            }
          >
            Закрыть смену
          </Button>
        )}
      </div>

      <Modal
        open={canClose && closeOpen}
        onClose={() => setCloseOpen(false)}
        title="Закрытие смены"
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="closing_cash">Наличные после пересчёта</Label>
            <Input
              id="closing_cash"
              type="text"
              inputMode="decimal"
              value={closingCash}
              onChange={(e) => {
                setClosingCash(e.target.value);
                if (closingCashError) setClosingCashError(null);
              }}
              invalid={closingCashError !== null}
              aria-describedby={closingCashError ? "closing-cash-error" : undefined}
              autoComplete="off"
              maxLength={15}
              autoFocus
            />
            {closingCashError ? (
              <p id="closing-cash-error" className="mt-1 text-xs text-danger" role="alert">
                {closingCashError}
              </p>
            ) : null}
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
            <Button
              aria-label="Подтвердить закрытие смены"
              onClick={() => void onClose()}
              isLoading={closeMutation.isPending}
              disabled={!online}
            >
              Закрыть
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}

const CASH_AMOUNT_ERROR = "Введите сумму от 0 до 999 999 999 999,99 с точностью до копейки";

function normalizeCashAmount(value: string): string | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(normalized)) return null;
  return normalized;
}
