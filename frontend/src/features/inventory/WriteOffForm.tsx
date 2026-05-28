import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  FormError,
  Input,
  Label,
  Select,
  Textarea,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { writeOffReasonLabel, writeOffReasonOptions } from "./labels";
import { useWriteOff } from "./queries";

const schema = z.object({
  qty: z
    .string()
    .min(1, "Введите количество")
    .refine((v) => Number(v) > 0, "Количество должно быть больше 0"),
  reason: z.enum(["expired", "damaged", "spoiled", "theft", "other"]),
  comment: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export function WriteOffForm({
  batchId,
  maxQty,
  onClose,
}: {
  batchId: string;
  maxQty: string;
  onClose: () => void;
}): JSX.Element {
  const writeOff = useWriteOff();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: { qty: "", reason: "expired", comment: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      return;
    }
    if (Number(parsed.data.qty) > Number(maxQty)) {
      form.setError("qty", { message: `Доступно не более ${maxQty}` });
      return;
    }
    setTopError(null);
    try {
      await writeOff.mutateAsync({
        batchId,
        payload: {
          qty: parsed.data.qty,
          reason: parsed.data.reason,
          comment: parsed.data.comment?.trim() || null,
        },
      });
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось списать"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-3 rounded-md border border-warning/40 bg-warning-subtle p-4">
      <p className="text-sm font-medium text-foreground">Списать из партии</p>
      <p className="text-xs text-foreground-muted">
        Доступно: <span className="font-mono">{maxQty}</span>
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="qty">Количество</Label>
          <Input
            id="qty"
            type="text"
            inputMode="decimal"
            invalid={Boolean(form.formState.errors.qty)}
            {...form.register("qty")}
          />
          <FormError>{form.formState.errors.qty?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="reason">Причина</Label>
          <Select id="reason" {...form.register("reason")}>
            {writeOffReasonOptions.map((r) => (
              <option key={r} value={r}>
                {writeOffReasonLabel[r]}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div>
        <Label htmlFor="comment">Комментарий</Label>
        <Textarea id="comment" {...form.register("comment")} />
      </div>
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" size="sm" isLoading={form.formState.isSubmitting}>
          Списать
        </Button>
      </div>
    </form>
  );
}
