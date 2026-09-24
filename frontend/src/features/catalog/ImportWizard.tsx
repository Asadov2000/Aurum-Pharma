import { useEffect, useRef, useState, type DragEvent } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Badge, Button, ConfirmDialog } from "@/components/ui";
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
  skip: "Не менять совпавшие товары",
  update: "Обновить совпавшие товары",
  create_copy: "Создать отдельные копии",
};

const strategyDescription: Record<DuplicateStrategy, string> = {
  skip: "Совпавшие товары останутся без изменений; будут добавлены только новые.",
  update: "Непустые данные совпавших товаров будут обновлены значениями из файла.",
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

const importColumns: Record<string, string> = {
  brand_name: "Название товара",
  inn: "МНН",
  manufacturer: "Производитель",
  form: "Форма выпуска",
  dosage: "Дозировка",
  pack_size: "Упаковка",
  atx_code: "Код АТХ",
  dispensing_type: "Условия отпуска",
  storage_type: "Условия хранения",
  category: "Категория",
  base_price: "Цена",
  barcode: "Штрихкод",
};

const MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024;

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
  canOpenIncoming?: boolean;
  storageKey: string;
}

export function ImportWizard({
  onClose,
  canRollback,
  canOpenIncoming = false,
  storageKey,
}: Props): JSX.Element {
  const [jobId, setJobId] = useState<string | null>(() => readStoredJob(storageKey));
  const [strategy, setStrategy] = useState<DuplicateStrategy>("skip");
  const [topError, setTopError] = useState<string | null>(null);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const [dragActive, setDragActive] = useState(false);
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
    if (fileRef.current) fileRef.current.value = "";
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".xlsx") && !lower.endsWith(".csv")) {
      setTopError("Выберите файл .xlsx или .csv. Старый формат .xls пересохраните как .xlsx.");
      return;
    }
    if (file.size === 0 || file.size > MAX_IMPORT_FILE_BYTES) {
      setTopError("Файл должен быть непустым и не больше 10 МБ.");
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

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    if (upload.isPending) return;
    if (event.dataTransfer.files.length !== 1) {
      setTopError("Выберите один файл для импорта.");
      return;
    }
    void onPick(event.dataTransfer.files[0] ?? null);
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
      <div className="space-y-5">
        <ImportSteps current={0} />
        <div className="rounded-lg border border-border bg-primary/5 p-4 text-sm text-foreground-secondary">
          <p className="font-semibold text-foreground">Заполнить каталог из файла</p>
          <p className="mt-1">
            Добавим карточки товаров: названия, цены и штрихкоды. Остатки на складе от этого не
            появятся.
          </p>
          {canOpenIncoming && (
            <p className="mt-2">
              Загружаете накладную с количеством и сроками?{" "}
              <Link className="font-semibold text-primary underline" to="/incoming">
                Перейти в «Приходы»
              </Link>
              .
            </p>
          )}
        </div>
        <div
          className={`rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
            dragActive ? "border-primary bg-primary/5" : "border-border bg-surface"
          }`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={onDrop}
        >
          <p className="font-semibold text-foreground">Перетащите Excel или CSV сюда</p>
          <p className="mt-1 text-sm text-foreground-muted">Файл .xlsx или .csv, до 10 МБ</p>
          <Button
            className="mt-4"
            variant="secondary"
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
          >
            Выбрать файл
          </Button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,text/csv"
          disabled={upload.isPending}
          aria-label="Файл каталога"
          onChange={(event) => void onPick(event.target.files?.[0] ?? null)}
          className="hidden"
        />
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <a
            className="font-semibold text-primary underline"
            href={`${import.meta.env.BASE_URL}templates/catalog-import.csv`}
            download="aurum-catalog-template.csv"
          >
            Скачать шаблон CSV
          </a>
          <span className="text-foreground-muted">
            Перед добавлением покажем строки и ошибки. Без подтверждения ничего не изменится.
          </span>
        </div>
        <details className="text-sm text-foreground-secondary">
          <summary className="cursor-pointer font-medium">Требования к своему файлу</summary>
          <p className="mt-2">
            Столбец с названием должен называться <code>brand_name</code>. Остальные столбцы можно
            взять из шаблона. Например: «Парацетамол 500 мг» в столбце названия, «25.50» в столбце
            цены. CSV поддерживает UTF-8 и Windows-1251.
          </p>
        </details>
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
        <ImportSteps current={job.status === "importing" || job.status === "success" ? 2 : 1} />
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

        {previewReady && (
          <p className="text-sm text-foreground-secondary">
            Корректных строк: {job.valid_rows}. Совпадения с каталогом будут учтены по выбранному
            ниже правилу. Строки с ошибками не добавятся. Товары пока не добавлены.
          </p>
        )}
        {job.status === "success" && (
          <p className="text-sm text-success-foreground" role="status">
            Импорт завершён. Обработано {job.valid_rows ?? 0} строк; совпавшие товары могли быть
            пропущены в зависимости от выбранного правила.
          </p>
        )}

        {job.preview_data && job.preview_data.length > 0 && <PreviewRows rows={job.preview_data} />}
        {job.errors && job.errors.length > 0 && (
          <ImportErrors rows={job.errors} total={job.error_rows ?? job.errors.length} />
        )}

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

        {job.status === "validating" && (
          <fieldset disabled={confirm.isPending} className="space-y-2">
            <legend className="mb-2 font-semibold text-foreground">
              Что делать с совпадениями?
            </legend>
            {(["skip", "update"] as const).map((option) => (
              <StrategyChoice
                key={option}
                option={option}
                selected={strategy === option}
                onChange={setStrategy}
              />
            ))}
            <details open={strategy === "create_copy" || undefined} className="text-sm">
              <summary className="cursor-pointer text-foreground-secondary">
                Дополнительный вариант
              </summary>
              <div className="mt-2">
                <StrategyChoice
                  option="create_copy"
                  selected={strategy === "create_copy"}
                  onChange={setStrategy}
                />
              </div>
            </details>
            {strategy !== "skip" && (
              <p className="text-sm text-warning-foreground" role="status">
                {strategy === "update"
                  ? "Внимание: данные совпавших карточек будут изменены. Отмена импорта не вернёт прежние значения."
                  : "Внимание: похожие товары могут появиться в каталоге повторно."}
              </p>
            )}
          </fieldset>
        )}
        <div className="flex flex-wrap justify-end gap-2">
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
  const availableColumns = [...new Set(visibleRows.flatMap((row) => Object.keys(row)))];
  const importantColumns = [
    "brand_name",
    "base_price",
    "barcode",
    "manufacturer",
    "dosage",
    "pack_size",
  ];
  const columns = [
    ...importantColumns.filter((column) => availableColumns.includes(column)),
    ...availableColumns.filter((column) => !importantColumns.includes(column)),
  ].slice(0, 6);
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
                {importColumns[column] ?? column}
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

function ImportErrors({
  rows,
  total,
}: {
  rows: Array<Record<string, unknown>>;
  total: number;
}): JSX.Element {
  const visibleRows = rows.slice(0, 10);
  return (
    <div className="max-h-48 overflow-auto rounded-md border border-danger/30 bg-danger-subtle p-3">
      <p className="mb-2 text-xs font-medium text-danger">
        Ошибки: {Math.max(total, rows.length)}. Показаны {visibleRows.length} первых.
      </p>
      <ol className="space-y-2 text-xs text-danger">
        {visibleRows.map((row, index) => {
          const messages = Array.isArray(row.messages) ? row.messages.filter(isErrorMessage) : [];
          if (messages.length === 0 && isErrorMessage(row.message)) messages.push(row.message);
          const source =
            typeof row.row === "number" && Number.isInteger(row.row) && row.row > 0
              ? `Строка ${row.row}`
              : isErrorMessage(row.row)
                ? `Товар: ${row.row}`
                : "Ошибка файла";
          return (
            <li key={index} className="break-words">
              <p className="font-medium">{source}</p>
              {messages.length > 0 ? (
                <ul className="list-disc space-y-1 pl-4">
                  {messages.map((message, messageIndex) => (
                    <li key={messageIndex}>{formatImportError(message)}</li>
                  ))}
                </ul>
              ) : (
                <p>Не удалось прочитать описание ошибки.</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function formatImportError(message: string): string {
  if (message === "brand_name is required") return "Укажите название товара.";
  if (/^base_price '.+' is not a number$/.test(message)) {
    return "Цена должна быть числом, например 25.50.";
  }
  return message.replace(
    /\b(brand_name|base_price|barcode|manufacturer|dosage|pack_size)\b/g,
    (field) => importColumns[field] ?? field,
  );
}

function ImportSteps({ current }: { current: 0 | 1 | 2 }): JSX.Element {
  return (
    <ol aria-label="Этапы импорта" className="grid grid-cols-3 gap-2 text-xs sm:text-sm">
      {["Выбрать файл", "Проверить", "Добавить"].map((label, index) => (
        <li
          key={label}
          aria-current={current === index ? "step" : undefined}
          className={`rounded-md border px-2 py-2 text-center ${
            current === index
              ? "border-primary bg-primary/5 font-semibold text-primary"
              : "border-border text-foreground-muted"
          }`}
        >
          {index + 1}. {label}
        </li>
      ))}
    </ol>
  );
}

function StrategyChoice({
  option,
  selected,
  onChange,
}: {
  option: DuplicateStrategy;
  selected: boolean;
  onChange: (strategy: DuplicateStrategy) => void;
}): JSX.Element {
  return (
    <label
      className={`flex cursor-pointer gap-3 rounded-md border p-3 text-sm ${
        selected ? "border-primary bg-primary/5" : "border-border"
      }`}
    >
      <input
        type="radio"
        name="duplicate-strategy"
        value={option}
        checked={selected}
        onChange={() => onChange(option)}
        className="mt-0.5 accent-primary"
      />
      <span>
        <span className="block font-semibold text-foreground">{strategyLabel[option]}</span>
        <span className="mt-0.5 block text-foreground-muted">{strategyDescription[option]}</span>
      </span>
    </label>
  );
}

function isErrorMessage(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
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
