import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import {
  formatSupplierDate,
  formatSupplierMoney,
  formatSupplierQuantity,
  supplierProductSubtitle,
} from "./formatters";
import { supplierReturnReasonLabel, supplierReturnReasonOptions } from "./labels";
import { useCreateSupplierReturn, useSupplierReturnCandidatesQuery } from "./queries";
import { type Supplier, type SupplierReturnCandidate, type SupplierReturnReason } from "./types";

const quantityPattern = /^(?:0|[1-9]\d{0,10})(?:[.,]\d{1,3})?$/;

const schema = z.object({
  batch_id: z.string().min(1, "Выберите партию"),
  source_document_id: z.string().min(1, "Не найден исходный приход"),
  qty: z
    .string()
    .trim()
    .min(1, "Введите количество")
    .regex(quantityPattern, "До 11 цифр и не более 3 знаков после запятой")
    .refine((value) => Number(value.replace(",", ".")) > 0, "Количество должно быть больше 0"),
  reason: z.enum(["damaged", "expired", "incorrect_delivery", "quality_issue", "other"], {
    required_error: "Выберите причину",
  }),
  comment: z.string().max(2000, "Не более 2000 символов").optional(),
});

interface FormValues {
  batch_id: string;
  source_document_id: string;
  qty: string;
  reason: SupplierReturnReason | "";
  comment: string;
}

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function SupplierReturnForm({
  supplier,
  onClose,
}: {
  supplier: Supplier;
  onClose: () => void;
}): JSX.Element {
  const operationId = useMemo(createOperationId, []);
  const createReturn = useCreateSupplierReturn();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<SupplierReturnCandidate | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  const form = useForm<FormValues>({
    defaultValues: {
      batch_id: "",
      source_document_id: "",
      qty: "",
      reason: "",
      comment: "",
    },
  });
  const candidates = useSupplierReturnCandidatesQuery({
    supplier_id: supplier.id,
    q: search || undefined,
    page: 1,
    page_size: 20,
  });
  const qty = form.watch("qty").replace(",", ".");
  const amount = selected ? Number(qty) * Number(selected.purchase_price) : 0;

  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput.trim()), 250);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const chooseCandidate = (candidate: SupplierReturnCandidate) => {
    setSelected(candidate);
    form.setValue("batch_id", candidate.batch_id, { shouldValidate: true });
    form.setValue("source_document_id", candidate.source_document_id, { shouldValidate: true });
    form.setValue("qty", "", { shouldDirty: true });
    form.clearErrors(["batch_id", "source_document_id", "qty"]);
  };

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        form.setError(path as keyof FormValues, { message: issue.message });
      }
      return;
    }
    if (!selected || selected.batch_id !== parsed.data.batch_id) {
      form.setError("batch_id", { message: "Выберите партию заново" });
      return;
    }
    const normalizedQty = parsed.data.qty.replace(",", ".");
    if (Number(normalizedQty) > Number(selected.qty_remaining)) {
      form.setError("qty", {
        message: `Доступно не более ${formatSupplierQuantity(selected.qty_remaining)}`,
      });
      return;
    }
    if (!online) {
      setTopError("Нет подключения к серверу. Возврат не будет записан офлайн.");
      return;
    }

    setTopError(null);
    try {
      await createReturn.mutateAsync({
        operation_id: operationId,
        supplier_id: supplier.id,
        batch_id: selected.batch_id,
        source_document_id: selected.source_document_id,
        qty: normalizedQty,
        reason: parsed.data.reason,
        comment: parsed.data.comment?.trim() || null,
      });
      onClose();
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось оформить возврат поставщику"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="supplier_return_search">Товар, партия или документ прихода</Label>
        <Input
          id="supplier_return_search"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Начните вводить или выберите из списка"
          autoComplete="off"
        />
      </div>

      <div className="max-h-64 overflow-y-auto rounded-lg border border-border" role="listbox">
        {candidates.isLoading ? (
          <p className="px-4 py-5 text-sm text-foreground-muted" role="status">
            Загрузка доступных партий…
          </p>
        ) : candidates.error ? (
          <div className="px-4 py-4 text-sm text-danger-foreground" role="alert">
            <p>{describeApiError(candidates.error, "Не удалось загрузить партии")}</p>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => void candidates.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : candidates.data?.items.length ? (
          candidates.data.items.map((candidate) => {
            const subtitle = supplierProductSubtitle(candidate);
            const active = selected?.batch_id === candidate.batch_id;
            return (
              <button
                key={candidate.batch_id}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => chooseCandidate(candidate)}
                className={cn(
                  "flex min-h-16 w-full items-start justify-between gap-4 border-b border-border px-4 py-3 text-left last:border-b-0",
                  active ? "bg-primary-subtle" : "hover:bg-foreground/[0.03]",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-foreground">
                    {candidate.catalog_name}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-foreground-muted">
                    {subtitle || "Без дополнительных характеристик"} · {candidate.branch_name}
                  </span>
                  <span className="mt-1 block text-xs text-foreground-muted">
                    Партия {candidate.batch_number ?? "без номера"} · приход{" "}
                    {candidate.document_number ?? "без номера"}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block font-mono text-sm font-semibold tabular-nums">
                    {formatSupplierQuantity(candidate.qty_remaining)}
                  </span>
                  <span className="block text-xs text-foreground-muted">
                    до {formatSupplierDate(candidate.expires_at)}
                  </span>
                </span>
              </button>
            );
          })
        ) : (
          <p className="px-4 py-5 text-sm italic text-foreground-muted">
            Нет принятых партий этого поставщика с доступным остатком.
          </p>
        )}
      </div>
      <FormError>{form.formState.errors.batch_id?.message}</FormError>

      {selected && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="supplier_return_qty">Количество</Label>
              <button
                type="button"
                className="text-xs font-medium text-primary hover:underline"
                onClick={() =>
                  form.setValue("qty", selected.qty_remaining, {
                    shouldDirty: true,
                    shouldValidate: true,
                  })
                }
              >
                Весь остаток
              </button>
            </div>
            <Input
              id="supplier_return_qty"
              inputMode="decimal"
              autoComplete="off"
              invalid={Boolean(form.formState.errors.qty)}
              {...form.register("qty")}
            />
            <FormError>{form.formState.errors.qty?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="supplier_return_reason">Причина</Label>
            <Select
              id="supplier_return_reason"
              invalid={Boolean(form.formState.errors.reason)}
              {...form.register("reason")}
            >
              <option value="">Выберите причину</option>
              {supplierReturnReasonOptions.map((reason) => (
                <option key={reason} value={reason}>
                  {supplierReturnReasonLabel[reason]}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.reason?.message}</FormError>
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="supplier_return_comment">Комментарий</Label>
            <Textarea
              id="supplier_return_comment"
              rows={3}
              placeholder="Например, номер акта или описание дефекта"
              invalid={Boolean(form.formState.errors.comment)}
              {...form.register("comment")}
            />
            <FormError>{form.formState.errors.comment?.message}</FormError>
          </div>
        </div>
      )}

      {selected && Number.isFinite(amount) && amount > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-warning-subtle px-4 py-3 text-sm text-warning-foreground">
          <span>Сумма по закупочной цене</span>
          <strong className="font-mono tabular-nums">
            {formatSupplierMoney(amount, selected.currency)}
          </strong>
        </div>
      )}

      {!online && (
        <p className="rounded-lg border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
          Нет подключения к серверу. Возврат станет доступен после восстановления связи.
        </p>
      )}
      {topError && (
        <p role="alert" className="text-sm text-danger">
          {topError}
        </p>
      )}

      <p className="text-xs leading-5 text-foreground-muted">
        Подтверждённый возврат уменьшает остаток партии и сохраняется как неизменяемая операция.
      </p>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" className="min-h-11" onClick={onClose}>
          Отмена
        </Button>
        <Button
          type="submit"
          className="min-h-11"
          disabled={!online || !selected}
          isLoading={form.formState.isSubmitting}
        >
          Оформить возврат
        </Button>
      </div>
    </form>
  );
}
