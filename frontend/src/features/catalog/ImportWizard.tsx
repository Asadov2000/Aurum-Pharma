import { useRef, useState } from "react";

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
  skip: "Пропустить",
  update: "Обновить",
  create_copy: "Создать копию",
};

const strategyOptions: DuplicateStrategy[] = ["skip", "update", "create_copy"];

const statusBadgeTone = (
  s: ImportJob["status"],
): "neutral" | "info" | "success" | "warning" | "danger" => {
  switch (s) {
    case "pending":
    case "validating":
      return "info";
    case "importing":
      return "warning";
    case "success":
      return "success";
    case "failed":
      return "danger";
    case "rolled_back":
      return "neutral";
    default:
      return "neutral";
  }
};

export function ImportWizard({ onClose }: { onClose: () => void }): JSX.Element {
  const [jobId, setJobId] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<DuplicateStrategy>("skip");
  const [topError, setTopError] = useState<string | null>(null);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = useUploadImport();
  const preview = usePreviewImport();
  const confirm = useConfirmImport();
  const rollback = useRollbackImport();

  // Poll while the job is running, otherwise stop.
  const jobQuery = useImportJobQuery(jobId, jobId ? 2000 : undefined);
  const job = jobQuery.data ?? null;
  const isPolling = job?.status === "importing";

  const onPick = async (file: File | null) => {
    if (!file) return;
    setTopError(null);
    // We accept .csv and .xlsx. The legacy binary .xls format can't be read
    // by openpyxl — reject it upfront with the same message the backend uses.
    const lower = file.name.toLowerCase();
    if (lower.endsWith(".xls") && !lower.endsWith(".xlsx")) {
      setTopError("Поддерживаются файлы .xlsx и .csv; пересохраните файл как .xlsx");
      return;
    }
    try {
      const j = await upload.mutateAsync(file);
      setJobId(j.id);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось загрузить файл"));
    }
  };

  const onPreview = async () => {
    if (!jobId) return;
    setTopError(null);
    try {
      await preview.mutateAsync(jobId);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось подготовить превью"));
    }
  };

  const onConfirm = async () => {
    if (!jobId) return;
    setTopError(null);
    try {
      await confirm.mutateAsync({ jobId, strategy });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось запустить импорт"));
    }
  };

  const onRollback = async () => {
    if (!jobId) return;
    setTopError(null);
    try {
      await rollback.mutateAsync(jobId);
      setRollbackOpen(false);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось откатить"));
    }
  };

  // ---- step 1: upload ----
  if (!job) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-foreground-secondary">
          Загрузите файл с позициями каталога — CSV (UTF-8 или Windows-1251) или Excel (.xlsx). Файл
          уйдёт в MinIO; на следующем шаге сервер построит превью.
        </p>
        <p className="text-xs text-foreground-muted">
          Обязательна только колонка «brand_name». Старый формат .xls не поддерживается —
          пересохраните как .xlsx.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,text/csv"
          onChange={(e) => void onPick(e.target.files?.[0] ?? null)}
          className="block w-full text-sm"
        />
        {topError && <p className="text-sm text-danger">{topError}</p>}
        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </div>
    );
  }

  // ---- step 2+ : we have a job ----
  return (
    <>
      <div className="space-y-4">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0 text-sm">
            <p className="truncate font-medium text-foreground">{job.source_filename}</p>
            <p className="text-xs text-foreground-muted">id: {job.id.slice(0, 8)}</p>
          </div>
          <Badge tone={statusBadgeTone(job.status)}>{job.status}</Badge>
        </div>

        {(job.total_rows ?? 0) > 0 && (
          <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
            <Stat label="Всего" value={job.total_rows ?? 0} />
            <Stat label="Корректных" value={job.valid_rows ?? 0} tone="success" />
            <Stat label="С ошибками" value={job.error_rows ?? 0} tone="danger" />
          </div>
        )}

        {job.preview_data && job.preview_data.length > 0 && (
          <div className="max-h-64 overflow-auto rounded-md border border-border bg-surface p-3">
            <p className="mb-2 text-xs font-medium text-foreground-muted">Первые строки превью</p>
            <pre className="text-xs leading-tight">
              {JSON.stringify(job.preview_data.slice(0, 5), null, 2)}
            </pre>
          </div>
        )}

        {job.errors && job.errors.length > 0 && (
          <div className="max-h-48 overflow-auto rounded-md border border-danger/30 bg-danger-subtle p-3">
            <p className="mb-2 text-xs font-medium text-danger">Ошибки ({job.errors.length})</p>
            <pre className="text-xs leading-tight text-danger">
              {JSON.stringify(job.errors.slice(0, 10), null, 2)}
            </pre>
          </div>
        )}

        {isPolling && (
          <p className="text-sm text-foreground-muted">
            Импорт выполняется… (обновление каждые 2 сек)
          </p>
        )}

        {topError && <p className="text-sm text-danger">{topError}</p>}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <Label htmlFor="strategy">Дубликаты</Label>
            <Select
              id="strategy"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as DuplicateStrategy)}
              disabled={job.status === "importing" || job.status === "success"}
              className="w-full sm:w-44"
            >
              {strategyOptions.map((s) => (
                <option key={s} value={s}>
                  {strategyLabel[s]}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex flex-wrap gap-2">
            {job.status === "pending" && (
              <Button onClick={() => void onPreview()} isLoading={preview.isPending}>
                Подготовить превью
              </Button>
            )}
            {job.status === "validating" && (
              <Button onClick={() => void onConfirm()} isLoading={confirm.isPending}>
                Запустить импорт
              </Button>
            )}
            {job.status === "success" && !job.rolled_back_at && (
              <Button
                variant="secondary"
                onClick={() => setRollbackOpen(true)}
                isLoading={rollback.isPending}
              >
                Откатить
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
        title="Откатить импорт"
        message="Все добавленные этим импортом позиции будут помечены удалёнными."
        confirmLabel="Откатить"
        variant="danger"
        isLoading={rollback.isPending}
        onConfirm={() => void onRollback()}
        onCancel={() => setRollbackOpen(false)}
      />
    </>
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
