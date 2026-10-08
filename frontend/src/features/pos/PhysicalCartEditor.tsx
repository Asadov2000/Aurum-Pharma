import { isAxiosError } from "axios";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label } from "@/components/ui";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";

import {
  deletePhysicalCartItem,
  getPhysicalCartQuote,
  putPhysicalCartItem,
  type PhysicalCartItem,
  type PhysicalCartQuote,
} from "./physicalCart";
import {
  getPhysicalCartOptions,
  type PhysicalCartOptions,
  type PhysicalSaleFormat,
} from "./physicalCartOptions";
import { type PhysicalCartRecovery } from "./physicalCartRecovery";

interface Props {
  saleId: string;
  branchId: string;
  items: PhysicalCartItem[];
  nameById: Record<string, string>;
  disabled: boolean;
  sessionIsActive: () => boolean;
  onChanged: () => Promise<void>;
  onNameChange?: (catalogId: string, name: string) => void;
  initialRecovery?: PhysicalCartRecovery | null;
  onRecoveryChange?: (operation: PhysicalCartRecovery | null) => void;
  onBusyChange: (busy: boolean) => void;
}

const formatNames: Record<PhysicalSaleFormat, string> = {
  pack: "Упаковка",
  blister: "Блистер",
  unit: "Поштучно",
};
const formSchema = z.object({
  catalogId: z.string().uuid(),
  format: z.enum(["pack", "blister", "unit"]),
  quantity: z
    .string()
    .regex(/^[1-9]\d*$/)
    .transform(Number)
    .pipe(z.number().int().max(1_000_000)),
});
interface FormValues {
  catalogId: string;
  format: PhysicalSaleFormat;
  quantity: string;
}
const defaults: FormValues = { catalogId: "", format: "pack", quantity: "1" };

/** Unpaid intent only: this editor does not allocate stock or accept a payment. */
export function PhysicalCartEditor(props: Props): JSX.Element {
  const { saleId, branchId, items, nameById, disabled } = props;
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(false);
  const ownerBusyChange = useRef(props.onBusyChange);
  const writing = useRef(false);
  const [initialRecovery] = useState(() => props.initialRecovery ?? null);
  const recovery = useRef<PhysicalCartRecovery | null>(initialRecovery);
  const [unresolved, setUnresolved] = useState(initialRecovery !== null);
  const itemId = useRef<string | null>(
    initialRecovery?.kind === "save" ? initialRecovery.itemId : null,
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState(
    initialRecovery?.kind === "save" ? initialRecovery.name : "",
  );
  const [options, setOptions] = useState<PhysicalCartOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [quote, setQuote] = useState<PhysicalCartQuote | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quoteError, setQuoteError] = useState(false);
  const [pickerVersion, setPickerVersion] = useState(0);
  const id = useId();
  const form = useForm<FormValues>({
    defaultValues:
      initialRecovery?.kind === "save"
        ? {
            catalogId: initialRecovery.payload.catalog_id,
            format: initialRecovery.payload.sale_format,
            quantity: String(initialRecovery.payload.quantity),
          }
        : defaults,
  });
  const catalogId = form.watch("catalogId");
  const format = form.watch("format");
  const active = useCallback(
    (): boolean =>
      mounted.current &&
      latest.current.saleId === saleId &&
      latest.current.branchId === branchId &&
      latest.current.sessionIsActive(),
    [saleId, branchId],
  );

  useEffect(() => {
    mounted.current = true;
    const releaseOwnerLock = ownerBusyChange.current;
    if (recovery.current) releaseOwnerLock(true);
    return () => {
      mounted.current = false;
      // Release only this mounted editor's lock. No late request may call a new owner.
      if (writing.current || recovery.current) releaseOwnerLock(false);
    };
  }, []);

  useEffect(() => {
    setOptions(null);
    if (!catalogId || disabled || !latest.current.sessionIsActive()) return;
    const controller = new AbortController();
    let cancelled = false;
    setLoadingOptions(true);
    void getPhysicalCartOptions(saleId, catalogId, controller.signal)
      .then((result) => {
        if (cancelled || !active() || form.getValues("catalogId") !== catalogId) return;
        if (
          result.sale_id !== saleId ||
          result.branch_id !== branchId ||
          result.catalog_id !== catalogId
        ) {
          setError("Не удалось проверить параметры товара. Выберите его ещё раз.");
          return;
        }
        setOptions(result);
      })
      .catch(() => {
        if (!cancelled && active())
          setError("Не удалось загрузить варианты продажи. Выберите товар ещё раз.");
      })
      .finally(() => {
        if (!cancelled && active()) setLoadingOptions(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [active, form, saleId, branchId, catalogId, disabled]);

  useEffect(() => {
    setQuote(null);
    setQuoteError(false);
    if (!items.length || disabled || !latest.current.sessionIsActive()) return;
    const controller = new AbortController();
    let cancelled = false;
    void getPhysicalCartQuote(saleId, controller.signal)
      .then((result) => {
        if (cancelled || !active()) return;
        if (result.quote.sale_id !== saleId || result.quote.branch_id !== branchId) {
          setQuoteError(true);
          return;
        }
        setQuote(result);
      })
      .catch(() => {
        if (!cancelled && active()) setQuoteError(true);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [active, items, saleId, branchId, disabled]);

  const resetEditor = (): void => {
    itemId.current = null;
    setEditingId(null);
    setSelectedName("");
    setOptions(null);
    form.reset(defaults);
    setPickerVersion((value) => value + 1);
  };
  const startWrite = (): boolean => {
    if (
      !active() ||
      latest.current.disabled ||
      writing.current ||
      items.some((item) => !item.physical_packaging)
    )
      return false;
    writing.current = true;
    setBusy(true);
    setError(null);
    latest.current.onBusyChange(true);
    return true;
  };
  const finishWrite = (): void => {
    if (!active()) return;
    writing.current = false;
    setBusy(false);
    latest.current.onBusyChange(recovery.current !== null);
  };
  const remember = (operation: PhysicalCartRecovery): void => {
    latest.current.onRecoveryChange?.(operation);
    recovery.current = operation;
    setUnresolved(true);
  };
  const acknowledge = (): void => {
    latest.current.onRecoveryChange?.(null);
    recovery.current = null;
    setUnresolved(false);
  };
  const save = form.handleSubmit(async (values) => {
    if (
      !active() ||
      latest.current.disabled ||
      writing.current ||
      recovery.current?.kind === "delete"
    )
      return;
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      setError("Выберите лекарство и укажите целое количество от 1 до 1 000 000.");
      return;
    }
    const option = options?.options.find((candidate) => candidate.sale_format === values.format);
    if (
      options?.catalog_id !== parsed.data.catalogId ||
      !option?.configured ||
      !option.quantity_step ||
      parsed.data.quantity % option.quantity_step
    ) {
      setError(
        option?.quantity_step
          ? `Количество должно быть кратно ${option.quantity_step}.`
          : "Этот вариант продажи не настроен в каталоге.",
      );
      return;
    }
    if (!startWrite()) return;
    // Keep this UUID after uncertain errors so retry updates the same line.
    itemId.current ??= crypto.randomUUID();
    const existing = items.find((item) => item.id === itemId.current);
    const retrying = recovery.current !== null;
    let writeAcknowledged = false;
    try {
      const operation: PhysicalCartRecovery =
        recovery.current?.kind === "save"
          ? recovery.current
          : {
              kind: "save",
              itemId: itemId.current,
              name: selectedName,
              payload: {
                catalog_id: parsed.data.catalogId,
                sale_format: parsed.data.format,
                quantity: parsed.data.quantity,
                position:
                  existing?.position ?? Math.max(0, ...items.map((item) => item.position)) + 1,
                discount_amount: existing?.discount_amount ?? "0.00",
              },
            };
      remember(operation);
      const saved = await putPhysicalCartItem(saleId, operation.itemId, operation.payload);
      if (!active()) return;
      if (saved.sale_id !== saleId || saved.branch_id !== branchId || saved.id !== itemId.current) {
        setError("Результат сохранения не подтверждён. Обновите корзину перед продолжением.");
        return;
      }
      writeAcknowledged = true;
      if (selectedName.trim()) {
        latest.current.onNameChange?.(parsed.data.catalogId, selectedName);
      }
      if (!active()) return;
      await latest.current.onChanged();
      if (!active()) return;
      acknowledge();
      resetEditor();
    } catch (saveError: unknown) {
      if (active()) {
        if (
          !retrying &&
          !writeAcknowledged &&
          isAxiosError(saveError) &&
          saveError.response &&
          saveError.response.status >= 400 &&
          saveError.response.status < 500 &&
          ![408, 429].includes(saveError.response.status)
        )
          acknowledge();
        setError(
          "Не удалось подтвердить сохранение. Повторите сохранение — строка не будет добавлена повторно.",
        );
      }
    } finally {
      finishWrite();
    }
  });
  const remove = async (item: Pick<PhysicalCartItem, "id">): Promise<void> => {
    if (
      recovery.current &&
      (recovery.current.kind !== "delete" || recovery.current.itemId !== item.id)
    )
      return;
    if (!startWrite()) return;
    try {
      remember({ kind: "delete", itemId: item.id });
      try {
        await deletePhysicalCartItem(saleId, item.id);
      } catch (deleteError: unknown) {
        // A repeat may find the already deleted row. Refresh still has to
        // authorize this sale before the recovery marker can be cleared.
        if (!isAxiosError(deleteError) || deleteError.response?.status !== 404) throw deleteError;
      }
      if (!active()) return;
      await latest.current.onChanged();
      if (!active()) return;
      acknowledge();
      if (editingId === item.id) resetEditor();
    } catch {
      if (active())
        setError("Не удалось подтвердить удаление. Обновите корзину и проверьте строку.");
    } finally {
      finishWrite();
    }
  };
  const selectedOption = options?.options.find((option) => option.sale_format === format);
  const locked =
    disabled || busy || !props.sessionIsActive() || items.some((item) => !item.physical_packaging);
  const controlsLocked = locked || unresolved;

  return (
    <section
      className="space-y-3 rounded-lg border border-border bg-surface p-3"
      aria-label="Продажа упаковками, блистерами и поштучно"
    >
      <div>
        <h3 className="font-semibold">Упаковки, блистеры и штуки</h3>
        <p className="text-sm text-foreground-muted">
          Выберите, в каком виде покупатель берёт лекарство. Корзина сохраняется на сервере; остатки
          пока не списываются.
        </p>
      </div>
      {items.some((item) => !item.physical_packaging) && (
        <p className="text-sm text-warning-foreground">
          В этой корзине есть позиции со старой настройкой. Здесь доступен только просмотр; новые
          форматы добавлять нельзя.
        </p>
      )}
      {!!items.length && (
        <ul className="divide-y divide-border">
          {items.map((item) => {
            const line = quote?.quote.lines.find((candidate) => candidate.id === item.id);
            const prepared = !item.physical_packaging;
            return (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div className="min-w-0">
                  <p className="font-medium">
                    {nameById[item.catalog_id] ?? "Лекарство из каталога"}
                  </p>
                  <p className="text-sm text-foreground-muted">
                    {formatNames[item.sale_format]} · {item.quantity} ·{" "}
                    {line ? `${line.net_amount} TJS` : "Не рассчитано"}
                  </p>
                  {prepared && (
                    <p className="text-sm text-warning">
                      Старая настройка поштучной продажи. Эту строку пока нельзя изменять здесь.
                    </p>
                  )}
                </div>
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={controlsLocked || prepared}
                    onClick={() => {
                      if (!active()) return;
                      itemId.current = item.id;
                      setEditingId(item.id);
                      setSelectedName(nameById[item.catalog_id] ?? "Лекарство из каталога");
                      setError(null);
                      form.reset({
                        catalogId: item.catalog_id,
                        format: item.sale_format,
                        quantity: String(item.quantity),
                      });
                      setPickerVersion((value) => value + 1);
                    }}
                  >
                    Изменить
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={controlsLocked || prepared}
                    onClick={() => void remove(item)}
                  >
                    Удалить
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <form onSubmit={(event) => void save(event)} className="space-y-3">
        <fieldset disabled={locked} className="space-y-3">
          <div>
            <Label htmlFor={`${id}-medicine`}>Название лекарства</Label>
            <fieldset disabled={controlsLocked} className="border-0 p-0">
              <CatalogPicker
                key={pickerVersion}
                id={`${id}-medicine`}
                value={catalogId}
                initialLabel={selectedName}
                branchId={branchId}
                clearable
                onChange={(value, name) => {
                  if (controlsLocked || !active()) return;
                  form.setValue("catalogId", value);
                  setSelectedName(name);
                  setError(null);
                }}
              />
            </fieldset>
          </div>
          <div role="radiogroup" aria-label="Как продавать" className="flex flex-wrap gap-2">
            {(["pack", "blister", "unit"] as const).map((value) => {
              const option = options?.options.find((candidate) => candidate.sale_format === value);
              return (
                <Button
                  key={value}
                  role="radio"
                  aria-checked={format === value}
                  variant={format === value ? "primary" : "secondary"}
                  disabled={controlsLocked || !option?.configured}
                  onClick={() => {
                    form.setValue("format", value);
                    form.setValue("quantity", String(option?.quantity_step ?? 1));
                    setError(null);
                  }}
                >
                  {formatNames[value]}
                </Button>
              );
            })}
          </div>
          {loadingOptions && (
            <p role="status" className="text-sm text-foreground-muted">
              Проверяем варианты продажи…
            </p>
          )}
          {options?.packaging && (
            <p className="text-sm text-foreground-muted">
              В упаковке: {options.packaging.units_per_pack} шт.
              {options.packaging.units_per_blister
                ? ` В блистере: ${options.packaging.units_per_blister} шт.`
                : " Размер блистера не указан."}{" "}
              Цена выбранного варианта:{" "}
              {selectedOption?.retail_unit_price
                ? `${selectedOption.retail_unit_price} TJS`
                : "не задана"}
              .
            </p>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-36">
              <Label htmlFor={`${id}-quantity`}>
                Количество
                {format === "pack" ? " упаковок" : format === "blister" ? " блистеров" : ", шт."}
              </Label>
              <Input
                readOnly={controlsLocked}
                id={`${id}-quantity`}
                type="number"
                min={selectedOption?.quantity_step ?? 1}
                step={selectedOption?.quantity_step ?? 1}
                max={1_000_000}
                {...form.register("quantity")}
              />
            </div>
            <Button
              type="submit"
              disabled={
                locked ||
                loadingOptions ||
                !selectedOption?.configured ||
                recovery.current?.kind === "delete"
              }
              isLoading={busy}
            >
              {unresolved
                ? "Повторить сохранение"
                : editingId
                  ? "Сохранить строку"
                  : "Добавить в корзину"}
            </Button>
            {catalogId && (
              <Button
                variant="ghost"
                disabled={controlsLocked}
                onClick={() => {
                  resetEditor();
                  setError(null);
                }}
              >
                Отменить
              </Button>
            )}
          </div>
          {selectedOption?.quantity_step && selectedOption.quantity_step > 1 && (
            <p className="text-sm text-foreground-muted">
              Можно продавать по {selectedOption.quantity_step}: {selectedOption.quantity_step},{" "}
              {selectedOption.quantity_step * 2}, {selectedOption.quantity_step * 3} и дальше.
            </p>
          )}
        </fieldset>
      </form>
      {unresolved && (
        <div className="space-y-2 text-sm text-warning-foreground">
          <p role="status">
            Результат операции ещё не подтверждён. Повторите её; новая строка не будет создана. До
            подтверждения корзину нельзя закрыть.
          </p>
          {recovery.current?.kind === "delete" && (
            <Button
              variant="secondary"
              disabled={locked}
              onClick={() => {
                const operation = recovery.current;
                if (operation?.kind === "delete") void remove({ id: operation.itemId });
              }}
            >
              Повторить удаление
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {!!items.length && (
        <p className="text-right font-semibold">
          Итого: {quote ? `${quote.quote.total_amount} TJS` : "Не рассчитано"}
        </p>
      )}
      {quoteError && (
        <p role="alert" className="text-sm text-danger">
          Не удалось рассчитать корзину. Цены и наличие нужно проверить перед оплатой.
        </p>
      )}
    </section>
  );
}
