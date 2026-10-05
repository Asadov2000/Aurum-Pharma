import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select, Switch } from "@/components/ui";
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
    has_blister: z.boolean(),
    units_per_blister: z.string(),
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
    if (!values.has_blister) return;
    const parsedBlister = integer(2).safeParse(values.units_per_blister);
    if (!parsedBlister.success) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["units_per_blister"],
        message: "Укажите целое число от 2 до 10 000",
      });
      return;
    }
    const blister = Number(parsedBlister.data);
    if (blister > pack || pack % blister !== 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["units_per_blister"],
        message:
          "В пачке должно быть целое число блистеров. Проверьте количество штук в пачке и блистере.",
      });
    }
  });

type Values = z.infer<typeof schema>;
const quantityNames: Record<Values["unit"], readonly [string, string, string]> = {
  tablet: ["таблетка", "таблетки", "таблеток"],
  capsule: ["капсула", "капсулы", "капсул"],
  ampoule: ["ампула", "ампулы", "ампул"],
  sachet: ["саше", "саше", "саше"],
  suppository: ["суппозиторий", "суппозитория", "суппозиториев"],
};
const quantityPlural = new Intl.PluralRules("ru");
function describeQuantity(quantity: number, unit: Values["unit"]): string {
  const plural = quantityPlural.select(quantity);
  return `${quantity} ${quantityNames[unit][plural === "one" ? 0 : plural === "few" ? 1 : 2]}`;
}
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
      has_blister: saved?.units_per_blister != null,
      units_per_blister: saved?.units_per_blister != null ? String(saved.units_per_blister) : "",
      unit_price: saved?.unit_price ?? "",
    },
  });
  const busy = update.isPending || form.formState.isSubmitting;
  const packValue = form.watch("units_per_pack").trim();
  const hasBlister = form.watch("has_blister");
  const blisterValue = form.watch("units_per_blister").trim();
  const pack = Number(packValue);
  const validPack = /^[1-9]\d{0,4}$/.test(packValue) && pack >= 2 && pack <= 10_000;
  const blister = Number(blisterValue);
  const validBlister =
    /^[1-9]\d{0,4}$/.test(blisterValue) && blister >= 2 && blister <= pack && pack % blister === 0;
  const unit = form.watch("unit");

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
      dispense_step: 1,
      units_per_blister: parsed.data.has_blister ? Number(parsed.data.units_per_blister) : null,
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
            <Label htmlFor="unit-preparation-pack">Сколько штук в упаковке?</Label>
            <Input
              id="unit-preparation-pack"
              inputMode="numeric"
              placeholder="Например, 30"
              aria-invalid={Boolean(form.formState.errors.units_per_pack)}
              {...form.register("units_per_pack")}
            />
            <FormError>{form.formState.errors.units_per_pack?.message}</FormError>
          </div>
          <div className="space-y-3 sm:col-span-2">
            <Switch label="В пачке есть блистеры (плажки)" {...form.register("has_blister")} />
            {hasBlister && (
              <div>
                <Label htmlFor="unit-preparation-blister">Сколько штук в одном блистере?</Label>
                <Input
                  id="unit-preparation-blister"
                  inputMode="numeric"
                  placeholder="Например, 10"
                  aria-describedby="unit-preparation-blister-help"
                  aria-invalid={Boolean(form.formState.errors.units_per_blister)}
                  {...form.register("units_per_blister")}
                />
                <p
                  id="unit-preparation-blister-help"
                  className="mt-1 text-xs text-foreground-muted"
                >
                  Посмотрите на упаковку и укажите фактическое количество. Например: 20 таблеток в
                  пачке, по 10 в блистере — всего 2 блистера.
                </p>
                <FormError>{form.formState.errors.units_per_blister?.message}</FormError>
              </div>
            )}
            {validPack && (!hasBlister || validBlister) && (
              <p className="rounded-lg border border-border bg-surface-raised p-3 text-sm">
                Пачка: {describeQuantity(pack, unit)}.
                {hasBlister &&
                  ` Блистер: ${describeQuantity(blister, unit)}. Количество блистеров в пачке: ${pack / blister}.`}
                {" Способ продажи выбирается в кассе, а не в каталоге."}
              </p>
            )}
            {saved && saved.dispense_step > 1 && (
              <p className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm">
                Раньше была подготовлена продажа только по {saved.dispense_step} шт. После
                сохранения состав будет подготовлен для выбора способа продажи в кассе, включая
                поштучный отпуск. Старые поставки не изменятся.
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="unit-preparation-price">Цена одной единицы, TJS</Label>
            <Input
              id="unit-preparation-price"
              inputMode="decimal"
              placeholder="Например, 1,50"
              aria-describedby="unit-preparation-price-help"
              aria-invalid={Boolean(form.formState.errors.unit_price)}
              {...form.register("unit_price")}
            />
            <p id="unit-preparation-price-help" className="mt-1 text-xs text-foreground-muted">
              Цена за одну штуку, даже если продаёте по несколько штук или целыми упаковками.
            </p>
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
