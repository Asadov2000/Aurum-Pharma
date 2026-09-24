import { useState } from "react";
import { useForm } from "react-hook-form";
import { isAxiosError } from "axios";
import { z } from "zod";

import { Button, FormError, Label, Select } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { createOperationId } from "@/lib/operationId";

import { importIncomingInvoice, previewIncomingInvoice } from "./api";
import {
  type InvoiceColumn,
  type InvoiceColumnMapping,
  type InvoicePreview,
  type InvoiceImportResult,
} from "./types";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const columns: { key: InvoiceColumn; label: string; required?: boolean }[] = [
  { key: "source_name", label: "Название товара", required: true },
  { key: "qty", label: "Количество" },
  { key: "purchase_price", label: "Закупочная цена" },
  { key: "sale_price", label: "Цена продажи" },
  { key: "batch_number", label: "Номер партии" },
  { key: "expires_at", label: "Срок годности" },
  { key: "manufactured_at", label: "Дата производства" },
];

const mappingSchema = z.object({
  source_name: z.string(),
  qty: z.string(),
  purchase_price: z.string(),
  sale_price: z.string(),
  batch_number: z.string(),
  expires_at: z.string(),
  manufactured_at: z.string(),
});

type MappingForm = z.infer<typeof mappingSchema>;

const emptyMapping: MappingForm = {
  source_name: "",
  qty: "",
  purchase_price: "",
  sale_price: "",
  batch_number: "",
  expires_at: "",
  manufactured_at: "",
};

function toMapping(values: MappingForm): InvoiceColumnMapping | null {
  if (values.source_name === "") return null;
  const selected = Object.values(values).filter(Boolean);
  if (new Set(selected).size !== selected.length) return null;
  return {
    source_name: Number(values.source_name),
    qty: values.qty === "" ? null : Number(values.qty),
    purchase_price: values.purchase_price === "" ? null : Number(values.purchase_price),
    sale_price: values.sale_price === "" ? null : Number(values.sale_price),
    batch_number: values.batch_number === "" ? null : Number(values.batch_number),
    expires_at: values.expires_at === "" ? null : Number(values.expires_at),
    manufactured_at: values.manufactured_at === "" ? null : Number(values.manufactured_at),
  };
}

export function InvoiceImportForm({
  documentId,
  onClose,
  onImported,
  onImportingChange,
}: {
  documentId: string;
  onClose: () => void;
  onImported: (result: InvoiceImportResult) => Promise<void>;
  onImportingChange: (importing: boolean) => void;
}): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<InvoicePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importAttempted, setImportAttempted] = useState(false);
  const [operationId, setOperationId] = useState(createOperationId);
  const [error, setError] = useState<string | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const form = useForm<MappingForm>({ defaultValues: emptyMapping });

  const chooseFile = (selected: File | null) => {
    setFile(selected);
    setPreview(null);
    setError(null);
    setMappingError(null);
    setImportAttempted(false);
    setOperationId(createOperationId());
    form.reset(emptyMapping);
  };

  const onPreview = async () => {
    if (!file || previewing) return;
    if (!/\.(csv|xlsx)$/i.test(file.name)) {
      setError("Выберите файл Excel (.xlsx) или CSV (.csv).");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setError("Файл слишком большой. Максимальный размер — 10 МБ.");
      return;
    }
    setPreviewing(true);
    setError(null);
    try {
      const result = await previewIncomingInvoice(documentId, file);
      setPreview(result);
      form.reset(
        Object.fromEntries(
          columns.map(({ key }) => [
            key,
            result.suggested_mapping[key] === null ? "" : String(result.suggested_mapping[key]),
          ]),
        ) as MappingForm,
      );
    } catch (cause) {
      setError(describeApiError(cause, "Не удалось прочитать накладную"));
    } finally {
      setPreviewing(false);
    }
  };

  const onImport = form.handleSubmit(async (values) => {
    if (!file || !preview || importing) return;
    const parsed = mappingSchema.safeParse(values);
    const mapping = parsed.success ? toMapping(parsed.data) : null;
    if (!mapping) {
      setMappingError(
        values.source_name === ""
          ? "Укажите столбец с названием товара."
          : "Каждый столбец можно назначить только одному полю.",
      );
      return;
    }
    setMappingError(null);
    setError(null);
    setImporting(true);
    onImportingChange(true);
    setImportAttempted(true);
    try {
      const result = await importIncomingInvoice(documentId, file, mapping, operationId);
      await onImported(result);
    } catch (cause) {
      const rejectedBeforeImport =
        isAxiosError(cause) &&
        cause.response !== undefined &&
        [400, 422].includes(cause.response.status);
      if (rejectedBeforeImport) {
        setImportAttempted(false);
        setOperationId(createOperationId());
        setError(describeApiError(cause, "Проверьте столбцы и строки накладной"));
      } else {
        setError(
          `${describeApiError(cause, "Не удалось загрузить накладную")}. Если ответ потерялся, повторите загрузку — дубли не создадутся.`,
        );
      }
    } finally {
      setImporting(false);
      onImportingChange(false);
    }
  });

  return (
    <div className="space-y-5">
      <p className="text-sm text-foreground-secondary">
        Загрузите CSV или Excel до 10 МБ. Строки попадут в незавершённые позиции: проверьте товар,
        упаковку, партию, срок и цены перед принятием на склад.
      </p>
      <div className="space-y-2">
        <Label htmlFor="incoming-invoice-file">Файл накладной</Label>
        <input
          id="incoming-invoice-file"
          type="file"
          accept=".csv,.xlsx"
          disabled={previewing || importing || importAttempted}
          className="block w-full rounded-md border border-input bg-surface px-3 py-3 text-sm file:mr-3 file:rounded file:border-0 file:bg-surface-subtle file:px-3 file:py-2 file:text-foreground"
          onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
        />
        {file && <p className="break-all text-xs text-foreground-muted">{file.name}</p>}
      </div>

      {!preview && (
        <Button
          type="button"
          variant="secondary"
          disabled={!file}
          isLoading={previewing}
          onClick={() => void onPreview()}
        >
          Проверить файл
        </Button>
      )}

      {preview && (
        <form onSubmit={onImport} className="space-y-5" noValidate>
          <div
            role="status"
            className="rounded-md border border-border bg-surface-subtle px-3 py-2 text-sm"
          >
            Найдено строк: {preview.total_rows}. Ни одна строка ещё не добавлена.
          </div>
          {preview.warnings.length > 0 && (
            <div
              role="alert"
              className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-sm"
            >
              {preview.warnings.map((warning, index) => (
                <p key={index}>{warning}</p>
              ))}
            </div>
          )}
          <fieldset disabled={importing || importAttempted} className="space-y-3">
            <legend className="font-semibold">Сопоставьте столбцы</legend>
            <p className="text-sm text-foreground-muted">
              Название обязательно. Остальные поля можно проверить и заполнить в черновиках.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {columns.map(({ key, label, required }) => (
                <div key={key}>
                  <Label htmlFor={`invoice-column-${key}`}>
                    {label}
                    {required ? " *" : ""}
                  </Label>
                  <Select
                    id={`invoice-column-${key}`}
                    {...form.register(key, { onChange: () => setMappingError(null) })}
                  >
                    <option value="">Не указано</option>
                    {preview.headers.map((header, index) => (
                      <option key={index} value={index}>
                        {index + 1}. {header || `Столбец ${index + 1}`}
                      </option>
                    ))}
                  </Select>
                </div>
              ))}
            </div>
          </fieldset>
          <FormError>{mappingError}</FormError>
          <div>
            <h3 className="mb-2 font-semibold">Первые строки файла</h3>
            <div
              className="max-h-60 overflow-auto rounded-md border border-border"
              tabIndex={0}
              aria-label="Предпросмотр строк накладной"
            >
              <table className="min-w-full text-left text-xs">
                <thead className="sticky top-0 bg-surface-subtle">
                  <tr>
                    {preview.headers.map((header, index) => (
                      <th
                        key={index}
                        scope="col"
                        className="whitespace-nowrap border-b border-border px-2 py-2"
                      >
                        {header || `Столбец ${index + 1}`}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-b border-border last:border-0">
                      {preview.headers.map((_, columnIndex) => (
                        <td key={columnIndex} className="max-w-52 break-words px-2 py-2 align-top">
                          {row[columnIndex] || "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" disabled={importing} onClick={onClose}>
              Закрыть
            </Button>
            <Button type="submit" isLoading={importing}>
              {importAttempted
                ? "Повторить загрузку"
                : `Добавить ${preview.total_rows} строк в черновик`}
            </Button>
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {!preview && (
        <div className="flex justify-end">
          <Button type="button" variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      )}
    </div>
  );
}
