import { lazy, Suspense, useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { Button, Input, Label } from "@/components/ui";
import { type CatalogItem } from "@/features/catalog/types";

import { catalogAmountPreview, quantitySchema } from "./catalogAmountPreview";

const NumPad = lazy(() => import("./NumPad"));
export function SelectedProductEditor({
  item,
  disabled,
  onAdd,
  onCancel,
}: {
  item: CatalogItem;
  disabled: boolean;
  onAdd: (quantity: number) => Promise<boolean>;
  onCancel: () => void;
}): JSX.Element {
  const form = useForm<{ quantity: string }>({ defaultValues: { quantity: "1" } });
  const [keypad, setKeypad] = useState(false);
  const [pending, setPending] = useState(false);
  const saving = useRef(false);
  const quantity = form.watch("quantity");
  const preview = catalogAmountPreview(item.base_price, quantity);
  const blocked = disabled || pending || form.formState.isSubmitting;
  const submit = form.handleSubmit(async (values) => {
    if (disabled || saving.current) return;
    const parsed = quantitySchema.safeParse(values.quantity.trim());
    if (!parsed.success) {
      form.setError("quantity", {
        message: "Введите количество больше нуля, до трёх знаков после запятой",
      });
      return;
    }
    saving.current = true;
    setPending(true);
    try {
      await onAdd(Number(parsed.data.replace(",", ".")));
    } finally {
      saving.current = false;
      setPending(false);
    }
  });
  return (
    <section className="pos-selected-product" aria-label="Добавление выбранного товара">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{item.brand_name}</h3>
          <p className="text-xs text-foreground-muted">
            {[item.dosage, item.pack_size].filter(Boolean).join(" · ")}
          </p>
        </div>
        <Button variant="ghost" size="sm" disabled={blocked} onClick={onCancel}>
          Отменить
        </Button>
      </div>
      <p className="text-sm text-foreground-muted">Обычный товар · количество в учётных единицах</p>
      <form onSubmit={(event) => void submit(event)} className="space-y-3">
        <Label htmlFor="selected-product-quantity">Количество</Label>
        <div className="flex gap-2">
          <Input
            id="selected-product-quantity"
            inputMode="decimal"
            disabled={blocked}
            aria-invalid={Boolean(form.formState.errors.quantity)}
            {...form.register("quantity")}
          />
          <Button
            type="button"
            variant="secondary"
            disabled={blocked}
            onClick={() => setKeypad(true)}
            aria-label="Цифровой ввод количества"
          >
            123
          </Button>
        </div>
        {form.formState.errors.quantity && (
          <p role="alert" className="text-sm text-danger">
            {form.formState.errors.quantity.message}
          </p>
        )}
        <p className="text-sm font-semibold text-primary" aria-live="polite">
          {preview
            ? `${quantity} × ${item.base_price?.replace(".", ",")} = ${preview} сомони`
            : "Цена будет проверена при добавлении"}
        </p>
        <p className="text-xs text-foreground-muted">
          Предварительно по базовой цене. Остаток и итоговую цену проверит сервер.
        </p>
        <Button
          type="submit"
          className="w-full"
          disabled={blocked}
          isLoading={pending || form.formState.isSubmitting}
        >
          Добавить в чек
        </Button>
      </form>
      {keypad && (
        <Suspense fallback={<p role="status">Загрузка цифрового ввода…</p>}>
          <NumPad
            title="Количество товара"
            initial={quantity}
            onClose={() => setKeypad(false)}
            onSubmit={(value) => {
              form.setValue("quantity", value);
              setKeypad(false);
            }}
          />
        </Suspense>
      )}
    </section>
  );
}
