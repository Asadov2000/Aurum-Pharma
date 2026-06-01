import { useEffect, useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";

import { useZReportQuery } from "./queries";
import { ZReportCard } from "./ZReportCard";

const LAST_CLOSED_KEY = "pos:lastClosedShiftId";

export function ReportsPage(): JSX.Element {
  const [input, setInput] = useState("");
  const [shiftId, setShiftId] = useState<string | null>(null);
  const lastClosed = window.localStorage.getItem(LAST_CLOSED_KEY);

  useEffect(() => {
    if (lastClosed && !input && !shiftId) setInput(lastClosed);
  }, [lastClosed, input, shiftId]);

  const onLoad = () => {
    const trimmed = input.trim();
    setShiftId(trimmed === "" ? null : trimmed);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Отчёты</h1>

      <Card>
        <CardHeader>
          <CardTitle>Z-отчёт по смене</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-96 max-w-full">
              <Label htmlFor="shift_id">ID смены</Label>
              <Input
                id="shift_id"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="UUID смены"
              />
              {lastClosed && lastClosed !== shiftId && (
                <p className="mt-1 text-xs text-foreground-muted">
                  Подставлен последний закрытый shift_id из этого браузера.
                </p>
              )}
            </div>
            <Button onClick={onLoad} disabled={!input.trim()}>
              Загрузить
            </Button>
          </div>
        </CardContent>
      </Card>

      {shiftId && <ZReportSection shiftId={shiftId} />}

      <Card>
        <CardHeader>
          <CardTitle>Другие отчёты</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-foreground-secondary">
            Сводка продаж за период, оборачиваемость склада и отчёт по списаниям
            появятся в Этапе 2 — для них нужны новые серверные эндпоинты.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function ZReportSection({ shiftId }: { shiftId: string }): JSX.Element {
  const { data, isLoading, error } = useZReportQuery(shiftId);
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  const onDownload = async () => {
    setDlError(null);
    setDownloading(true);
    try {
      const blob = await getZReportXlsx(shiftId);
      downloadBlob(blob, `z-report-${shiftId}.xlsx`);
    } catch (err) {
      setDlError(describeApiError(err, "Не удалось скачать Z-отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  if (isLoading) return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  if (error) {
    return (
      <p className="text-sm text-danger">
        {describeApiError(error, "Не удалось загрузить отчёт")}
      </p>
    );
  }
  if (!data) return <p className="text-sm text-foreground-muted">Нет данных</p>;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
          Скачать Z-отчёт (XLSX)
        </Button>
      </div>
      {dlError && <p className="text-sm text-danger">{dlError}</p>}
      <ZReportCard report={data} />
    </div>
  );
}
