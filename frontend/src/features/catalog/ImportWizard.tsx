import { useEffect, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { Badge, Button, ConfirmDialog, Label, Select } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import {
  useConfirmImport,
  useImportJobQuery,
  usePreviewImport,
  useRollbackImport,
  useUploadImport,
} from "./queries";
import { type DuplicateStrategy, type ImportJob } from "./types";

const strategyLabel: Record<DuplicateStrategy, string> = {
  skip: "Не изменять существующие товары",
  update: "Обновить существующие товары",
  create_copy: "Добавить как новые товары",
};

const strategyDescription: Record<DuplicateStrategy, string> = {
  skip: "Совпавшие товары останутся без изменений; будут добавлены только новые.",
  update: "Данные совпавших товаров будут заменены значениями из файла.",
  create_copy: "Каждая строка будет добавлена отдельно, даже если похожий товар уже существует.",
};

const statusLabel: Record<ImportJob["status"], string> = {
  pending: "Файл загружен",
  previewing: "Проверка файла",
  validating: "Превью готово",
  importing: "Импорт выполняется",
  success: "Импорт завершён",
  failed: "Ошибка импорта",
  rolled_back: "Импорт отменён",
};

const strategyOptions: DuplicateStrategy[] = ["skip", "update", "create_copy"];

const statusBadgeTone = (
  status: ImportJob["status"],
): "neutral" | "info" | "success" | "warning" | "danger" => {
  if (status === "pending" || status === "validating") return "info";
  if (status === "previewing" || status === "importing") return "warning";
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "neutral";
};

function readStoredJob(storageKey: string): string | null {
  try {
    return window.localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function storeJob(storageKey: string, jobId: string | null): void {
  try {
    if (jobId) window.localStorage.setItem(storageKey, jobId);
    else window.localStorage.removeItem(storageKey);
  } catch {
    // Import remains usable when browser storage is unavailable.
  }
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return "Сложное значение";
  return String(value);
}

interface Props {
  onClose: () => void;
  canRollback: boolean;
  storageKey: string;
}

export function ImportWizard({ onClose, canRollback, storageKey }: Props): JSX.Element {
  const [jobId, setJobId] = useState<string | null>(() => readStoredJob(storageKey));
  const [strategy, setStrategy] = useState<DuplicateStrategy>("skip");
  const [topError, setTopError] = useState<string | null>(null);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const actionPending = useRef(false);
  const refreshedCompletedJob = useRef<string | null>(null);
  const queryClient = useQueryClient();

  const upload = useUploadImport();
  const preview = usePreviewImport();
  const confirm = useConfirmImport();
  const rollback = useRollbackImport();
  const jobQuery = useImportJobQuery(jobId);
  const job = jobQuery.data ?? null;
  const previewReady =
    job?.status === "validating" &&
    job.total_rows !== null &&
    job.valid_rows !== null &&
    job.error_rows !== null &&
    job.preview_data !== null;
  const canConfirm = previewReady && (job?.valid_rows ?? 0) > 0;

  useEffect(() => {
    if (job?.status !== "success" || refreshedCompletedJob.current === job.id) return;
    refreshedCompletedJob.current = job.id;
    void queryClient.invalidateQueries({ queryKey: ["catalog", "list"] });
    void queryClient.invalidateQueries({ queryKey: ["catalog", "summary"] });
    void queryClient.invalidateQueries({ queryKey: ["catalog", "picker"] });
  }, [job?.id, job?.status, queryClient]);

  const resetJob = () => {
    setJobId(null);
    storeJob(storageKey, null);
    setTopError(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const onPick = async (file: File | null) => {
    if (!file) return;
    setTopError(null);
    const lower = file.name.toLowerCase();
    if (lower.endsWith(".xls") && !lower.endsWith(".xlsx")) {
      setTopError("Поддерживаются файлы .xlsx и .csv; пересохраните файл как .xlsx");
      return;
    }
    try {
      const uploadedJob = await upload.mutateAsync(file);
      setJobId(uploadedJob.id);
      storeJob(storageKey, uploadedJob.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось загрузить файл"));
    }
  };

  const onPreview = async () => {
    if (!jobId || job?.status !== "pending" || actionPending.current) return;
    actionPending.current = true;
    setTopError(null);
    try {
      await preview.mutateAsync(jobId);
    } catch (err) {
      // A lost POST response does not mean the server rejected the background job.
      const refreshed = await jobQuery.refetch();
      if (!refreshed.data || refreshed.data.status === "pending") {
        setTopError(describeApiError(err, "Не удалось запустить проверку файла"));
      }
    } finally {
      actionPending.current = false;
    }
  };

  const onConfirm = async () => {
    if (!jobId || !canConfirm || actionPending.current) return;
    actionPending.current = true;
    setTopError(null);
    try {
      await confirm.mutateAsync({ jobId, strategy });
    } catch (err) {
      const refreshed = await jobQuery.refetch();
      if (!refreshed.data || refreshed.data.status === "validating") {
        setTopError(describeApiError(err, "Не удалось запустить импорт"));
      }
    } finally {
      actionPending.current = false;
    }
  };

  const onRollback = async () => {
    if (!jobId) return;
    setTopError(null);
    try {
      await rollback.mutateAsync(jobId);
      setRollbackOpen(false);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось откатить импорт"));
    }
  };

  if (jobId && jobQuery.isPending) {
    return (
      <p className="py-8 text-center text-sm text-foreground-muted" role="status">
        {jobQuery.fetchStatus === "paused"
          ? "Ожидаем соединение, чтобы открыть сохранённый импорт…"
          : "Загрузка импорта…"}
      </p>
    );
  }

  if (jobId && jobQuery.error && !job) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-danger" role="alert">
          {describeApiError(jobQuery.error, "Не удалось открыть сохранённый импорт")}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={() => void jobQuery.refetch()}>
            Повторить
          </Button>
          <Button onClick={resetJob}>Загрузить новый файл</Button>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="space-y-4">
        <div className="space-y-1 text-sm text-foreground-secondary">
          <p>Загрузите CSV или Excel (.xlsx). Сначала система покажет превью и ошибки.</p>
          <p className="text-xs text-foreground-muted">
            Обязательна колонка «brand_name» (торговое название). CSV поддерживает UTF-8 и
            Windows-1251.
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,text/csv"
          disabled={upload.isPending}
          aria-label="Файл каталога"
          onChange={(event) => void onPick(event.target.files?.[0] ?? null)}
          className="block w-full text-sm"
        />
        {upload.isPending && <p className="text-sm text-foreground-muted">Загрузка файла…</p>}
        {topError && (
          <p className="text-sm text-danger" role="alert">
            {topError}
          </p>
        )}
        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose} disabled={upload.isPending}>
            Закрыть
          </Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex min-w-0 items-start justify-between gap-3 border-b border-border pb-3">
          <div className="min-w-0 text-sm">
            <p className="truncate font-medium text-foreground">{job.source_filename}</p>
            <p className="text-xs text-foreground-muted">Импорт {job.id.slice(0, 8)}</p>
          </div>
          <Badge tone={statusBadgeTone(job.status)}>{statusLabel[job.status]}</Badge>
        </div>

        {(job.total_rows ?? 0) > 0 && (
          <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
            <Stat label="Всего строк" value={job.total_rows ?? 0} />
            <Stat label="Корректных" value={job.valid_rows ?? 0} tone="success" />
            <Stat label="С ошибками" value={job.error_rows ?? 0} tone="danger" />
          </div>
        )}

        {job.preview_data && job.preview_data.length > 0 && <PreviewRows rows={job.preview_data} />}
        {job.errors && job.errors.length > 0 && <ImportErrors rows={job.errors} />}

        {(job.status === "previewing" || job.status === "importing") && (
          <p className="text-sm text-foreground-muted" aria-live="polite">
            {job.status === "previewing"
              ? "Проверяем файл в фоне. Товары пока не добавляются. "
              : "Файл обрабатывается в фоне. "}
            Это окно можно закрыть: при следующем открытии импорт продолжится с текущего шага.
          </p>
        )}

        {job.status === "failed" && (
          <p className="text-sm text-danger" role="alert">
            Обработка файла не завершена. Проверьте ошибки и загрузите файл заново.
          </p>
        )}
        {previewReady && !canConfirm && (
          <p className="text-sm text-danger" role="alert">
            В файле нет корректных строк для импорта. Исправьте ошибки и загрузите новый файл.
          </p>
        )}
        {jobQuery.error && (
          <p className="text-sm text-danger" role="alert">
            Не удалось обновить состояние импорта. Проверим снова после восстановления связи.
          </p>
        )}

        {topError && (
          <p className="text-sm text-danger" role="alert">
            {topError}
          </p>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          {(job.status === "pending" || job.status === "validating") && (
            <div>
              <Label htmlFor="strategy">Что делать с дубликатами</Label>
              <Select
                id="strategy"
                value={strategy}
                disabled={preview.isPending || confirm.isPending}
                onChange={(event) => setStrategy(event.target.value as DuplicateStrategy)}
                className="w-full sm:w-48"
              >
                {strategyOptions.map((option) => (
                  <option key={option} value={option}>
                    {strategyLabel[option]}
                  </option>
                ))}
              </Select>
              <p className="mt-1 max-w-md text-xs text-foreground-muted">
                {strategyDescription[strategy]}
              </p>
            </div>
          )}
          <div className="ml-auto flex flex-wrap justify-end gap-2">
            {job.status === "pending" && (
              <Button onClick={() => void onPreview()} isLoading={preview.isPending}>
                Проверить файл
              </Button>
            )}
            {job.status === "validating" && (
              <Button
                onClick={() => void onConfirm()}
                isLoading={confirm.isPending}
                disabled={!canConfirm}
              >
                Импортировать товары
              </Button>
            )}
            {job.status === "success" && !job.rolled_back_at && canRollback && (
              <Button variant="secondary" onClick={() => setRollbackOpen(true)}>
                Отменить результаты импорта
              </Button>
            )}
            {(job.status === "success" ||
              job.status === "failed" ||
              job.status === "rolled_back" ||
              (previewReady && !canConfirm)) && (
              <Button variant="secondary" onClick={resetJob}>
                Новый импорт
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={rollbackOpen}
        title="Отменить результаты импорта?"
        message="Товары, созданные этим импортом, будут перенесены в архив. Товары, которые были обновлены, не изменятся обратно."
        confirmLabel="Перенести созданные товары в архив"
        variant="danger"
        isLoading={rollback.isPending}
        onConfirm={() => void onRollback()}
        onCancel={() => setRollbackOpen(false)}
      />
    </>
  );
}

function PreviewRows({ rows }: { rows: Array<Record<string, unknown>> }): JSX.Element {
  const visibleRows = rows.slice(0, 5);
  const columns = [...new Set(visibleRows.flatMap((row) => Object.keys(row)))].slice(0, 6);
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[520px] text-left text-xs">
        <caption className="px-3 py-2 text-left font-medium text-foreground-muted">
          Первые строки файла
        </caption>
        <thead className="border-y border-border bg-background">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium text-foreground-muted">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {visibleRows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-56 break-words px-3 py-2 text-foreground">
                  {displayValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImportErrors({ rows }: { rows: Array<Record<string, unknown>> }): JSX.Element {
  return (
    <div className="max-h-48 overflow-auto rounded-md border border-danger/30 bg-danger-subtle p-3">
      <p className="mb-2 text-xs font-medium text-danger">Ошибки ({rows.length})</p>
      <ol className="space-y-2 text-xs text-danger">
        {rows.slice(0, 10).map((row, index) => (
          <li key={index} className="break-words">
            {Object.entries(row)
              .map(([key, value]) => `${key}: ${displayValue(value)}`)
              .join(" · ")}
          </li>
        ))}
      </ol>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "success" | "danger";
}): JSX.Element {
  const toneClass =
    tone === "success"
      ? "text-success-foreground"
      : tone === "danger"
        ? "text-danger"
        : "text-foreground";
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={`text-lg font-semibold ${toneClass}`}>{value}</p>
    </div>
  );
}
