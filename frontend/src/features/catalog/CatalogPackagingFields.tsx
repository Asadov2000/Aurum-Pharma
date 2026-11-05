import { type UseFormReturn } from "react-hook-form";

import { Button, FormError, Input, Label, Select, Switch } from "@/components/ui";

import { CatalogTemplateField } from "./CatalogTemplateField";
import { type FormValues } from "./CatalogItemForm";
import { unitPreparationSchema } from "./packagingForm";
import { type UnitSalePreparation } from "./types";

export function CatalogPackagingFields({
  form,
  canPrepare,
  saved,
}: {
  form: UseFormReturn<FormValues>;
  canPrepare: boolean;
  saved: UnitSalePreparation | null | undefined;
}): JSX.Element {
  const enabled = form.watch("packaging_enabled");
  const hasBlister = form.watch("packaging_has_blister");
  const packValue = form.watch("packaging_units_per_pack");
  const unit = form.watch("packaging_unit");
  const blisterValue = form.watch("packaging_units_per_blister");
  const description = form.watch("pack_size");
  const validComposition = unitPreparationSchema.safeParse({
    unit,
    units_per_pack: packValue,
    has_blister: hasBlister,
    units_per_blister: blisterValue,
    unit_price: "1.00",
  }).success;
  const pack = Number(packValue);
  const blister = Number(blisterValue);
  return (
    <section
      aria-labelledby="catalog-packaging-title"
      className="space-y-4 rounded-xl border border-border bg-surface-subtle p-4"
    >
      <h3 id="catalog-packaging-title" className="font-semibold">
        Упаковка и цена
      </h3>
      <section
        aria-labelledby="catalog-whole-pack-title"
        className="space-y-3 rounded-lg border border-border bg-surface-raised p-3"
      >
        <h4 id="catalog-whole-pack-title" className="font-semibold">
          Целая упаковка
        </h4>
        <div className="grid gap-4 sm:grid-cols-2">
          {enabled ? (
            <fieldset disabled={!canPrepare} className="space-y-3">
              <div>
                <Label htmlFor="catalog-pack-count">Размер упаковки</Label>
                <div className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2">
                  <Input
                    id="catalog-pack-count"
                    aria-label="Количество в упаковке"
                    inputMode="numeric"
                    placeholder="Например, 20"
                    invalid={Boolean(form.formState.errors.packaging_units_per_pack)}
                    {...form.register("packaging_units_per_pack")}
                  />
                  <Select aria-label="Что в упаковке" {...form.register("packaging_unit")}>
                    <option value="tablet">таблеток</option>
                    <option value="capsule">капсул</option>
                    <option value="ampoule">ампул</option>
                    <option value="sachet">саше</option>
                    <option value="suppository">суппозиториев</option>
                  </Select>
                </div>
                <p className="mt-1 text-xs text-foreground-muted">
                  Общее количество во всей пачке, включая все блистеры.
                  {!saved && description && ` Прежнее описание: «${description}».`}
                </p>
                <FormError>{form.formState.errors.packaging_units_per_pack?.message}</FormError>
              </div>
            </fieldset>
          ) : (
            <CatalogTemplateField
              kind="pack_size"
              label="Размер упаковки"
              placeholder="Например, 100 мл"
              value={form.watch("pack_size") ?? ""}
              inputProps={form.register("pack_size")}
            />
          )}
          <div>
            <Label htmlFor="base_price">Цена целой упаковки, TJS</Label>
            <Input
              id="base_price"
              inputMode="decimal"
              placeholder="0.00"
              invalid={Boolean(form.formState.errors.base_price)}
              {...form.register("base_price")}
            />
            <FormError>{form.formState.errors.base_price?.message}</FormError>
          </div>
        </div>
      </section>
      <section
        aria-labelledby="catalog-piece-title"
        className="space-y-3 rounded-lg border border-border bg-surface-raised p-3"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 id="catalog-piece-title" className="font-semibold">
            Поштучно и блистерами
          </h4>
          {canPrepare && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                form.setValue("packaging_enabled", !enabled, { shouldDirty: true });
                form.clearErrors([
                  "packaging_units_per_pack",
                  "packaging_units_per_blister",
                  "packaging_unit_price",
                ]);
              }}
            >
              {enabled ? "Убрать настройку" : "Добавить настройку"}
            </Button>
          )}
        </div>
        {!enabled && (
          <p className="text-sm text-foreground-muted">
            Дополнительная настройка для продажи части упаковки. Продажа целой пачкой сохраняется.
          </p>
        )}
        {enabled && (
          <div>
            <Label htmlFor="catalog-unit-price">Цена одной штуки, TJS</Label>
            <Input
              id="catalog-unit-price"
              inputMode="decimal"
              placeholder="Например, 1,50"
              disabled={!canPrepare}
              invalid={Boolean(form.formState.errors.packaging_unit_price)}
              {...form.register("packaging_unit_price")}
            />
            <FormError>{form.formState.errors.packaging_unit_price?.message}</FormError>
          </div>
        )}
        {enabled && (
          <fieldset disabled={!canPrepare} className="space-y-3">
            <Switch
              label="В пачке есть блистеры (плажки)"
              {...form.register("packaging_has_blister")}
            />
            {hasBlister && (
              <div>
                <Label htmlFor="catalog-blister-count">Штук в одном блистере</Label>
                <Input
                  id="catalog-blister-count"
                  inputMode="numeric"
                  placeholder="Например, 10"
                  invalid={Boolean(form.formState.errors.packaging_units_per_blister)}
                  {...form.register("packaging_units_per_blister")}
                />
                <FormError>{form.formState.errors.packaging_units_per_blister?.message}</FormError>
              </div>
            )}
          </fieldset>
        )}
        {enabled && (
          <>
            {hasBlister && validComposition && (
              <p className="text-sm text-foreground-muted">
                Всего блистеров в пачке: {pack / blister}.
              </p>
            )}
            {saved && saved.dispense_step > 1 && (
              <p className="text-sm text-foreground-muted">
                Прежняя настройка: продажа по {saved.dispense_step} шт. Изменение состава или цены
                подготовит выбор количества от одной штуки.
              </p>
            )}
            <p className="text-xs text-foreground-muted">
              Это подготовка карточки. Поштучная продажа в кассе пока недоступна.
            </p>
          </>
        )}
      </section>
    </section>
  );
}
