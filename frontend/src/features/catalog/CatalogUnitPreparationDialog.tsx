import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select } from "@/components/ui";
import { permissionBranchScope } from "@/features/auth/branchPermissions";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";

import { useCatalogItemQuery, useUpdateCatalogItem } from "./queries";
import { type CatalogItem, type UnitSalePreparation } from "./types";

const integer = (minimum: number) =>
  z
    .string()
    .trim()
    .refine(
      (value) =>
        /^[1-9]\d{0,4}$/.test(value) && Number(value) >= minimum && Number(value) <= 10_000,
      `Введите целое число от ${minimum} до 10 000`,
    );

const schema = z
  .object({
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    units_per_pack: integer(2),
    dispense_step: integer(1),
    unit_price: z
      .string()
      .trim()
      .refine(
        (value) => /^(0|[1-9]\d{0,11})([.,]\d{1,2})?$/.test(value) && /[1-9]/.test(value),
        "Введите цену больше нуля, не более 12 цифр до запятой и двух после",
      ),
  })
  .superRefine((values, context) => {
    const pack = Number(values.units_per_pack);
    const step = Number(values.dispense_step);
    if (Number.isInteger(pack) && Number.isInteger(step) && step > 0 && pack % step !== 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["dispense_step"],
        message: "Шаг отпуска должен делить упаковку без остатка",
      });
    }
  });

type Values = z.infer<typeof schema>;
interface Props {
  itemId: string;
  onClose: () => void;
}

export function CatalogUnitPreparationDialog({ itemId, onClose }: Props): JSX.Element {
  const { user } = useAuth();
  const canUpdate =
    hasPermission(user, "catalog.update") && permissionBranchScope(user, "catalog.update") === null;
  if (!canUpdate) {
    return (
      <Modal open onClose={onClose} title="Подготовка поштучного отпуска">
        <p>Для изменения этой настройки нужен доступ к редактированию каталога всей организации.</p>
        <Button type="button" variant="secondary" onClick={onClose}>
          Закрыть
        </Button>
      </Modal>
    );
  }
  return <PreparationLoader itemId={itemId} onClose={onClose} />;
}

function PreparationLoader({ itemId, onClose }: Props): JSX.Element {
  const query = useCatalogItemQuery(itemId, true);
  if (query.isError) {
    return (
      <Modal open onClose={onClose} title="Подготовка поштучного отпуска">
        <FormError>
          {describeApiError(query.error, "Не удалось загрузить карточку лекарства.")}
        </FormError>
        <Button type="button" variant="secondary" onClick={() => void query.refetch()}>
          Повторить
        </Button>
      </Modal>
    );
  }
  // Wait for a fresh GET even when the editor has cached an older card.
  if (!query.data || !query.isFetchedAfterMount) {
    return (
      <Modal open onClose={onClose} title="Подготовка поштучного отпуска">
        <p role="status">Загружаем настройку лекарства…</p>
      </Modal>
    );
  }
  return <PreparationForm key={itemId} item={query.data} onClose={onClose} />;
}

function PreparationForm({
  item,
  onClose,
}: {
  item: CatalogItem;
  onClose: () => void;
}): JSX.Element {
  const saved = item.unit_sale_preparation;
  const update = useUpdateCatalogItem();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<Values>({
    defaultValues: {
      unit: saved?.unit ?? "tablet",
      units_per_pack: saved ? String(saved.units_per_pack) : "",
      dispense_step: saved ? String(saved.dispense_step) : "1",
      unit_price: saved?.unit_price ?? "",
    },
  });
  const busy = update.isPending || form.formState.isSubmitting;

  async function persist(preparation: UnitSalePreparation | null): Promise<void> {
    setError(null);
    try {
      await update.mutateAsync({ id: item.id, payload: { unit_sale_preparation: preparation } });
      onClose();
    } catch (cause) {
      setError(
        describeApiError(
          cause,
          "Не удалось сохранить настройку. Проверьте соединение и повторите.",
        ),
      );
    }
  }

  const submit = form.handleSubmit(async (values) => {
    form.clearErrors();
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        form.setError(issue.path[0] as keyof Values, { message: issue.message });
      }
      return;
    }
    const [whole, fraction = ""] = parsed.data.unit_price.replace(",", ".").split(".");
    await persist({
      version: 1,
      state: "prepared",
      unit: parsed.data.unit,
      units_per_pack: Number(parsed.data.units_per_pack),
      dispense_step: Number(parsed.data.dispense_step),
      unit_price: `${whole}.${fraction.padEnd(2, "0")}`,
    });
  });

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Подготовка поштучного отпуска"
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.stopPropagation();
          void submit(event);
        }}
        className="space-y-4"
      >
        <p className="font-medium">{item.brand_name}</p>
        <p
          role="status"
          className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm"
        >
          {saved ? "Настройка подготовлена. " : "Настройка для будущей поштучной продажи. "}
          Продажа поштучно ещё недоступна. Сохранение не меняет остатки и цену упаковки.
        </p>
        <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="unit-preparation-unit">Единица отпуска</Label>
            <Select id="unit-preparation-unit" {...form.register("unit")}>
              <option value="tablet">Таблетка</option>
              <option value="capsule">Капсула</option>
              <option value="ampoule">Ампула</option>
              <option value="sachet">Саше</option>
              <option value="suppository">Суппозиторий</option>
            </Select>
            <FormError>{form.formState.errors.unit?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="unit-preparation-pack">Единиц в упаковке</Label>
            <Input
              id="unit-preparation-pack"
              inputMode="numeric"
              placeholder="Например, 30"
              aria-invalid={Boolean(form.formState.errors.units_per_pack)}
              {...form.register("units_per_pack")}
            />
            <FormError>{form.formState.errors.units_per_pack?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="unit-preparation-step">Шаг отпуска</Label>
            <Input
              id="unit-preparation-step"
              inputMode="numeric"
              aria-invalid={Boolean(form.formState.errors.dispense_step)}
              {...form.register("dispense_step")}
            />
            <p className="mt-1 text-xs text-foreground-muted">
              1 — по одной единице; 10 — только кратно десяти.
            </p>
            <FormError>{form.formState.errors.dispense_step?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="unit-preparation-price">Цена одной единицы, TJS</Label>
            <Input
              id="unit-preparation-price"
              inputMode="decimal"
              placeholder="Например, 1,50"
              aria-invalid={Boolean(form.formState.errors.unit_price)}
              {...form.register("unit_price")}
            />
            <FormError>{form.formState.errors.unit_price?.message}</FormError>
          </div>
        </fieldset>
        <FormError>{error}</FormError>
        <div className="flex flex-wrap justify-end gap-2">
          {saved && (
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => void persist(null)}
            >
              Убрать подготовку
            </Button>
          )}
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" disabled={busy} isLoading={busy}>
            Сохранить подготовку
          </Button>
        </div>
      </form>
    </Modal>
  );
}
