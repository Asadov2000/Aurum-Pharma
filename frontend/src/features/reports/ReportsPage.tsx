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
      <h1 className="text-2xl font-semibold text-slate-900">Отчёты</h1>

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
                <p className="mt-1 text-xs text-slate-500">
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
          <p className="text-sm text-slate-600">
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

  if (isLoading) return <p className="text-sm text-slate-500">Загрузка…</p>;
  if (error) {
    return (
      <p className="text-sm text-red-600">
        {describeApiError(error, "Не удалось загрузить отчёт")}
      </p>
    );
  }
  if (!data) return <p className="text-sm text-slate-500">Нет данных</p>;
  return <ZReportCard report={data} />;
}
