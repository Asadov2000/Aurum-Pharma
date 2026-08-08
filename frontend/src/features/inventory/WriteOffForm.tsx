import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { formatInventoryMoney, formatInventoryQuantity } from "./formatters";
import { writeOffReasonLabel, writeOffReasonOptions } from "./labels";
import { useWriteOff } from "./queries";
import { type WriteOffReason } from "./types";

const quantityPattern = /^(?:0|[1-9]\d{0,10})(?:[.,]\d{1,3})?$/;

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const schema = z.object({
  qty: z
    .string()
    .trim()
    .min(1, "Введите количество")
    .regex(quantityPattern, "До 11 цифр и не более 3 знаков после запятой")
    .refine((value) => Number(value.replace(",", ".")) > 0, "Количество должно быть больше 0"),
  reason: z.enum(["expired", "damaged", "spoiled", "theft", "other"], {
    required_error: "Выберите причину",
  }),
  comment: z.string().max(2000, "Не более 2000 символов").optional(),
});

interface FormValues {
  qty: string;
  reason: WriteOffReason | "";
  comment: string;
}

export function WriteOffForm({
  batchId,
  maxQty,
  purchasePrice,
  currency,
  productName,
  batchNumber,
  onClose,
}: {
  batchId: string;
  maxQty: string;
  purchasePrice: string;
  currency: string;
  productName: string;
  batchNumber: string | null;
  onClose: () => void;
}): JSX.Element {
  const writeOff = useWriteOff();
  const operationId = useMemo(createOperationId, []);
  const [topError, setTopError] = useState<string | null>(null);
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  const form = useForm<FormValues>({
    defaultValues: { qty: "", reason: "", comment: "" },
  });
  const qty = form.watch("qty");
  const normalizedQty = qty.replace(",", ".");
  const amount = Number(normalizedQty) * Number(purchasePrice);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

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

    const parsedQty = parsed.data.qty.replace(",", ".");
    if (Number(parsedQty) > Number(maxQty)) {
      form.setError("qty", { message: `Доступно не более ${formatInventoryQuantity(maxQty)}` });
      return;
    }
    if (!online) {
      setTopError("Нет подключения к серверу. Списание будет доступно после восстановления связи.");
      return;
    }

    setTopError(null);
    try {
      await writeOff.mutateAsync({
        batchId,
        payload: {
          operation_id: operationId,
          qty: parsedQty,
          reason: parsed.data.reason,
          comment: parsed.data.comment?.trim() || null,
        },
      });
      onClose();
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось списать товар"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="rounded-lg border border-border bg-background px-4 py-3">
        <p className="font-medium text-foreground">{productName}</p>
        <p className="mt-1 text-sm text-foreground-muted">
          Партия {batchNumber ?? "без номера"} · доступно {formatInventoryQuantity(maxQty)}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="write_off_qty">Количество</Label>
            <button
              type="button"
              className="text-xs font-medium text-primary hover:underline"
              onClick={() => {
                form.setValue("qty", maxQty, { shouldDirty: true, shouldValidate: true });
                form.clearErrors("qty");
              }}
            >
              Весь остаток
            </button>
          </div>
          <Input
            id="write_off_qty"
            type="text"
            inputMode="decimal"
            autoComplete="off"
            invalid={Boolean(form.formState.errors.qty)}
            aria-describedby={form.formState.errors.qty ? "write_off_qty_error" : undefined}
            {...form.register("qty")}
          />
          <FormError id="write_off_qty_error">{form.formState.errors.qty?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="write_off_reason">Причина</Label>
          <Select
            id="write_off_reason"
            invalid={Boolean(form.formState.errors.reason)}
            {...form.register("reason")}
          >
            <option value="">Выберите причину</option>
            {writeOffReasonOptions.map((reason) => (
              <option key={reason} value={reason}>
                {writeOffReasonLabel[reason]}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.reason?.message}</FormError>
        </div>
      </div>

      <div>
        <Label htmlFor="write_off_comment">Комментарий</Label>
        <Textarea
          id="write_off_comment"
          rows={3}
          placeholder="Дополнительные сведения для акта"
          invalid={Boolean(form.formState.errors.comment)}
          {...form.register("comment")}
        />
        <FormError>{form.formState.errors.comment?.message}</FormError>
      </div>

      {Number.isFinite(amount) && amount > 0 && (
        <div className="flex items-center justify-between gap-4 rounded-lg bg-warning-subtle px-4 py-3 text-sm">
          <span className="text-warning-foreground">Сумма списания по закупочной цене</span>
          <strong className="whitespace-nowrap font-mono tabular-nums text-warning-foreground">
            {formatInventoryMoney(amount, currency)}
          </strong>
        </div>
      )}

      {!online && (
        <p className="rounded-lg border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
          Нет подключения к серверу. Остаток не будет изменён офлайн.
        </p>
      )}
      {topError && (
        <p role="alert" className="text-sm text-danger">
          {topError}
        </p>
      )}

      <p className="text-xs leading-5 text-foreground-muted">
        После подтверждения остаток уменьшится, а в истории появится неизменяемая операция.
      </p>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" className="min-h-11" onClick={onClose}>
          Отмена
        </Button>
        <Button
          type="submit"
          className="min-h-11"
          disabled={!online}
          isLoading={form.formState.isSubmitting}
        >
          Подтвердить списание
        </Button>
      </div>
    </form>
  );
}
