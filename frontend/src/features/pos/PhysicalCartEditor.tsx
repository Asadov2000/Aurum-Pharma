import { isAxiosError } from "axios";
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
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
import { type SaleItem } from "./types";

const NumPad = lazy(() => import("./NumPad"));

export interface PhysicalCartSelection {
  catalogId: string;
  name: string;
  requestId: string;
  options: PhysicalCartOptions;
}

interface Props {
  saleId: string;
  branchId: string;
  items: PhysicalCartItem[];
  legacyItems?: readonly Pick<SaleItem, "id" | "sale_id" | "catalog_id" | "qty">[];
  onTotalChange?: (total: string | null) => void;
  hideTotal?: boolean;
  nameById: Record<string, string>;
  disabled: boolean;
  sessionIsActive: () => boolean;
  onChanged: () => Promise<void>;
  onNameChange?: (catalogId: string, name: string) => void;
  initialRecovery?: PhysicalCartRecovery | null;
  initialFormat?: PhysicalSaleFormat;
  selection?: PhysicalCartSelection | null;
  onSelectionCancel?: () => void;
  onSelectionSaved?: () => void;
  onEditingChange?: (editing: boolean) => void;
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

const selectionFormatNames: Record<PhysicalSaleFormat, string> = {
  unit: "Штуки",
  blister: "Блистеры",
  pack: "Упаковки",
};

function canonicalLegacyQuantity(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{1,11}(\.\d{1,3})?$/.test(value)) return null;
  const [whole = "0", fraction = ""] = value.split(".");
  const padded = fraction.padEnd(3, "0");
  if (BigInt(whole + padded) === 0n) return null;
  return `${BigInt(whole).toString()}.${padded}`;
}

function matchesRowOptions(
  snapshot: PhysicalCartOptions | null | undefined,
  item: PhysicalCartItem,
  saleId: string,
  branchId: string,
): snapshot is PhysicalCartOptions {
  return (
    !!snapshot &&
    snapshot.sale_id === saleId &&
    snapshot.branch_id === branchId &&
    snapshot.catalog_id === item.catalog_id &&
    snapshot.tenant_id === item.tenant_id &&
    snapshot.currency === "TJS"
  );
}

function quantityStep(
  snapshot: PhysicalCartOptions | null | undefined,
  format: PhysicalSaleFormat,
): number | null {
  const option = snapshot?.options.find((candidate) => candidate.sale_format === format);
  const step = option?.quantity_step;
  return option?.configured &&
    typeof step === "number" &&
    Number.isInteger(step) &&
    step > 0 &&
    step <= 1_000_000
    ? step
    : null;
}

function previewAmount(quantity: string, price: string | null | undefined): string | null {
  if (!/^[1-9]\d{0,6}$/.test(quantity) || Number(quantity) > 1_000_000) return null;
  if (!price || !/^\d{1,12}\.\d{2}$/.test(price)) return null;
  const amount = BigInt(price.replace(".", "")) * BigInt(quantity);
  return `${amount / 100n}.${String(amount % 100n).padStart(2, "0")}`;
}

/** Unpaid intent only: this editor does not allocate stock or accept a payment. */
export function PhysicalCartEditor(props: Props): JSX.Element {
  const { saleId, branchId, items, nameById, disabled } = props;
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(false);
  const ownerBusyChange = useRef(props.onBusyChange);
  const ownerEditingChange = useRef(props.onEditingChange);
  const editing = useRef(false);
  const writing = useRef(false);
  const entryFormat = useRef(props.initialFormat ?? "pack");
  const quantityEntered = useRef(false);
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
  const [rowOptions, setRowOptions] = useState<Record<string, PhysicalCartOptions | null>>({});
  const rowOptionsCache = useRef<Record<string, PhysicalCartOptions | null>>({});
  const rowOptionsOwner = useRef({ saleId, branchId });
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [quote, setQuote] = useState<PhysicalCartQuote | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quoteError, setQuoteError] = useState(false);
  const [pickerVersion, setPickerVersion] = useState(0);
  const externalMode = props.selection !== undefined;
  const consumedRequest = useRef<string | null>(null);
  const externalEntry = useRef(false);
  const acceptedSelection = useRef<PhysicalCartSelection | null>(null);
  const [formatChosen, setFormatChosen] = useState(!externalMode || initialRecovery !== null);
  const [showNumPad, setShowNumPad] = useState(false);
  const id = useId();
  const form = useForm<FormValues>({
    defaultValues:
      initialRecovery?.kind === "save"
        ? {
            catalogId: initialRecovery.payload.catalog_id,
            format: initialRecovery.payload.sale_format,
            quantity: String(initialRecovery.payload.quantity),
          }
        : { ...defaults, format: entryFormat.current },
  });
  const catalogId = form.watch("catalogId");
  const format = form.watch("format");
  const quantity = form.watch("quantity");
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
    const releaseEditingLock = ownerEditingChange.current;
    if (recovery.current) releaseOwnerLock(true);
    return () => {
      mounted.current = false;
      // Release only this mounted editor's lock. No late request may call a new owner.
      if (writing.current || recovery.current) releaseOwnerLock(false);
      if (editing.current) releaseEditingLock?.(false);
    };
  }, []);

  useEffect(() => {
    const selection = props.selection;
    if (selection === undefined || selection?.requestId === consumedRequest.current) return;
    // A selection received during recovery/edit/write must never replay later.
    consumedRequest.current = selection?.requestId ?? null;
    if (writing.current || recovery.current || editingId || disabled || !active()) return;
    itemId.current = null;
    externalEntry.current = selection !== null;
    acceptedSelection.current = selection;
    quantityEntered.current = false;
    setShowNumPad(false);
    setFormatChosen(false);
    setError(null);
    setSelectedName(selection?.name ?? "");
    form.reset({ ...defaults, catalogId: selection?.catalogId ?? "" });
  }, [props.selection, editingId, disabled, active, form]);

  useEffect(() => {
    if (
      rowOptionsOwner.current.saleId !== saleId ||
      rowOptionsOwner.current.branchId !== branchId
    ) {
      rowOptionsOwner.current = { saleId, branchId };
      rowOptionsCache.current = {};
      setRowOptions({});
    }
    if (!externalMode || disabled || !active()) return;
    const selected = acceptedSelection.current;
    if (
      selected &&
      selected.options.sale_id === saleId &&
      selected.options.branch_id === branchId &&
      selected.options.catalog_id === selected.catalogId &&
      selected.options.currency === "TJS" &&
      rowOptionsCache.current[selected.catalogId] !== selected.options
    ) {
      rowOptionsCache.current = {
        ...rowOptionsCache.current,
        [selected.catalogId]: selected.options,
      };
      setRowOptions(rowOptionsCache.current);
    }
    const catalogs = [
      ...new Set(items.filter((item) => item.physical_packaging).map((item) => item.catalog_id)),
    ];
    const owns = (result: PhysicalCartOptions, catalog: string) =>
      result.sale_id === saleId &&
      result.branch_id === branchId &&
      result.catalog_id === catalog &&
      result.currency === "TJS" &&
      items
        .filter((item) => item.catalog_id === catalog)
        .every((item) => item.tenant_id === result.tenant_id);
    const missing = catalogs.filter((catalog) => {
      const cached = rowOptionsCache.current[catalog];
      return cached === undefined || (cached !== null && !owns(cached, catalog));
    });
    if (!missing.length) return;
    const controller = new AbortController();
    let cancelled = false;
    // Read only missing catalogs, sequentially: large receipts must not fan out requests.
    void (async () => {
      for (const catalog of missing) {
        let result: PhysicalCartOptions | null = null;
        try {
          const response = await getPhysicalCartOptions(saleId, catalog, controller.signal);
          if (owns(response, catalog)) result = response;
        } catch {
          // The row keeps explicit edit available; quick controls remain disabled.
        }
        if (cancelled || !active()) return;
        rowOptionsCache.current = { ...rowOptionsCache.current, [catalog]: result };
        setRowOptions(rowOptionsCache.current);
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [active, externalMode, disabled, items, saleId, branchId, props.selection]);

  useEffect(() => {
    setOptions(null);
    setLoadingOptions(false);
    if (!catalogId || disabled || !latest.current.sessionIsActive()) return;
    const rowSnapshot = rowOptions[catalogId];
    const editedItem = items.find((item) => item.id === editingId);
    if (
      externalMode &&
      editedItem &&
      matchesRowOptions(rowSnapshot, editedItem, saleId, branchId)
    ) {
      setOptions(rowSnapshot);
      return;
    }
    if (externalMode && !editingId && !recovery.current) {
      const selection = acceptedSelection.current;
      if (!selection || !externalEntry.current || selection.catalogId !== catalogId) return;
      const result = selection.options;
      if (
        result.sale_id !== saleId ||
        result.branch_id !== branchId ||
        result.catalog_id !== catalogId
      ) {
        setError("Не удалось проверить параметры товара. Выберите его ещё раз.");
        return;
      }
      setOptions(result);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setLoadingOptions(true);
    void getPhysicalCartOptions(saleId, catalogId, controller.signal)
      .then((result) => {
        if (cancelled || !active() || form.getValues("catalogId") !== catalogId) return;
        if (
          result.sale_id !== saleId ||
          result.branch_id !== branchId ||
          result.catalog_id !== catalogId ||
          (editedItem && !matchesRowOptions(result, editedItem, saleId, branchId))
        ) {
          setError("Не удалось проверить параметры товара. Выберите его ещё раз.");
          return;
        }
        setOptions(result);
        if (
          externalMode &&
          editingId &&
          result.currency === "TJS" &&
          latest.current.items
            .filter((item) => item.catalog_id === catalogId)
            .every((item) => item.tenant_id === result.tenant_id)
        ) {
          rowOptionsCache.current = { ...rowOptionsCache.current, [catalogId]: result };
          setRowOptions(rowOptionsCache.current);
        }
        const option = result.options.find(
          (candidate) => candidate.sale_format === form.getValues("format"),
        );
        if (
          !itemId.current &&
          !recovery.current &&
          !quantityEntered.current &&
          option?.configured &&
          option.quantity_step
        ) {
          form.setValue("quantity", String(option.quantity_step));
        }
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
  }, [
    active,
    form,
    saleId,
    branchId,
    catalogId,
    disabled,
    externalMode,
    editingId,
    props.selection,
    rowOptions,
    items,
  ]);

  const legacyItems = props.legacyItems;
  useEffect(() => {
    const notifyTotal = latest.current.onTotalChange;
    setQuote(null);
    setQuoteError(false);
    notifyTotal?.(null);
    if (!items.length || disabled || busy || unresolved || !latest.current.sessionIsActive())
      return;
    const controller = new AbortController();
    let cancelled = false;
    void getPhysicalCartQuote(saleId, controller.signal)
      .then((result) => {
        if (cancelled || !active()) return;
        const physicalLines = result.quote.lines.filter(
          (line) => line.inventory_kind !== "legacy_pack",
        );
        const legacyLines = result.quote.lines.filter(
          (line) => line.inventory_kind === "legacy_pack",
        );
        const physicalMatches =
          physicalLines.length === items.length &&
          items.every(
            (item) =>
              item.sale_id === saleId &&
              item.branch_id === branchId &&
              item.tenant_id === result.quote.tenant_id &&
              physicalLines.some(
                (line) =>
                  line.id === item.id &&
                  line.catalog_id === item.catalog_id &&
                  line.quantity === item.quantity &&
                  line.sale_format === item.sale_format &&
                  line.discount_amount === item.discount_amount,
              ),
          );
        const legacyMatches =
          legacyItems === undefined ||
          (legacyLines.length === legacyItems.length &&
            legacyItems.every(
              (item) =>
                item.sale_id === saleId &&
                legacyLines.some(
                  (line) =>
                    line.id === item.id &&
                    line.catalog_id === item.catalog_id &&
                    typeof line.quantity === "string" &&
                    canonicalLegacyQuantity(line.quantity) !== null &&
                    canonicalLegacyQuantity(line.quantity) === canonicalLegacyQuantity(item.qty),
                ),
            ));
        if (
          result.quote.sale_id !== saleId ||
          result.quote.branch_id !== branchId ||
          result.quote.currency !== "TJS" ||
          !/^\d{1,12}\.\d{2}$/.test(result.quote.total_amount) ||
          new Set(result.quote.lines.map((line) => line.id)).size !== result.quote.lines.length ||
          !physicalMatches ||
          !legacyMatches
        ) {
          setQuoteError(true);
          return;
        }
        setQuote(result);
        notifyTotal?.(result.quote.total_amount);
      })
      .catch(() => {
        if (!cancelled && active()) setQuoteError(true);
      });
    return () => {
      cancelled = true;
      controller.abort();
      notifyTotal?.(null);
    };
  }, [active, items, legacyItems, saleId, branchId, disabled, busy, unresolved]);

  const resetEditor = (): void => {
    itemId.current = null;
    if (editing.current) {
      editing.current = false;
      latest.current.onEditingChange?.(false);
    }
    setEditingId(null);
    setSelectedName("");
    setOptions(null);
    quantityEntered.current = false;
    externalEntry.current = false;
    acceptedSelection.current = null;
    setFormatChosen(!externalMode);
    setShowNumPad(false);
    form.reset({ ...defaults, format: entryFormat.current });
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
  const performSave = async (
    operation: Extract<PhysicalCartRecovery, { kind: "save" }>,
  ): Promise<void> => {
    const retrying = recovery.current !== null;
    let writeAcknowledged = false;
    try {
      remember(operation);
      const saved = await putPhysicalCartItem(saleId, operation.itemId, operation.payload);
      if (!active()) return;
      if (
        saved.sale_id !== saleId ||
        saved.branch_id !== branchId ||
        saved.id !== operation.itemId
      ) {
        setError("Результат сохранения не подтверждён. Обновите корзину перед продолжением.");
        return;
      }
      writeAcknowledged = true;
      if (operation.name.trim()) {
        latest.current.onNameChange?.(operation.payload.catalog_id, operation.name);
      }
      if (!active()) return;
      await latest.current.onChanged();
      if (!active()) return;
      acknowledge();
      const savedSelection = externalEntry.current;
      resetEditor();
      if (savedSelection) latest.current.onSelectionSaved?.();
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
        ) {
          acknowledge();
          if (externalMode) {
            const refreshedOptions = { ...rowOptionsCache.current };
            refreshedOptions[operation.payload.catalog_id] = null;
            rowOptionsCache.current = refreshedOptions;
            setRowOptions(refreshedOptions);
          }
        }
        setError(
          "Не удалось подтвердить сохранение. Повторите сохранение — строка не будет добавлена повторно.",
        );
      }
    } finally {
      finishWrite();
    }
  };

  const save = form.handleSubmit(async (values) => {
    if (
      !active() ||
      latest.current.disabled ||
      writing.current ||
      recovery.current?.kind === "delete"
    )
      return;
    if (!formatChosen) {
      setError("Выберите единицу продажи: штуки, блистеры или упаковки.");
      return;
    }
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      setError("Выберите лекарство и укажите целое количество от 1 до 1 000 000.");
      return;
    }
    const option = options?.options.find((candidate) => candidate.sale_format === values.format);
    if (
      options?.sale_id !== saleId ||
      options.branch_id !== branchId ||
      options.catalog_id !== parsed.data.catalogId ||
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
    const operation: Extract<PhysicalCartRecovery, { kind: "save" }> =
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
    await performSave(operation);
  });

  const changeRowQuantity = async (item: PhysicalCartItem, direction: -1 | 1): Promise<void> => {
    if (
      editing.current ||
      externalEntry.current ||
      recovery.current ||
      latest.current.items.find((current) => current.id === item.id) !== item
    )
      return;
    const snapshot = rowOptions[item.catalog_id];
    const step = quantityStep(snapshot, item.sale_format);
    if (!matchesRowOptions(snapshot, item, saleId, branchId) || !step || item.quantity % step)
      return;
    const nextQuantity = item.quantity + direction * step;
    if (nextQuantity < step || nextQuantity > 1_000_000 || !startWrite()) return;
    const name = nameById[item.catalog_id] ?? "Лекарство из каталога";
    itemId.current = item.id;
    editing.current = true;
    latest.current.onEditingChange?.(true);
    setEditingId(item.id);
    setSelectedName(name);
    setFormatChosen(true);
    setOptions(snapshot);
    form.reset({
      catalogId: item.catalog_id,
      format: item.sale_format,
      quantity: String(nextQuantity),
    });
    await performSave({
      kind: "save",
      itemId: item.id,
      name,
      payload: {
        catalog_id: item.catalog_id,
        sale_format: item.sale_format,
        quantity: nextQuantity,
        position: item.position,
        discount_amount: item.discount_amount,
      },
    });
  };
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
  const selectedOption = formatChosen
    ? options?.options.find((option) => option.sale_format === format)
    : undefined;
  const amount = previewAmount(quantity, selectedOption?.retail_unit_price);
  const showEditor = !externalMode || !!catalogId || unresolved;
  const locked =
    disabled || busy || !props.sessionIsActive() || items.some((item) => !item.physical_packaging);
  const controlsLocked = locked || unresolved;
  const rowControlsLocked =
    controlsLocked || (externalMode && (externalEntry.current || editing.current));
  const cancelEditor = (): void => {
    if (controlsLocked || !active()) return;
    const cancelledSelection = externalEntry.current;
    resetEditor();
    setError(null);
    if (cancelledSelection) latest.current.onSelectionCancel?.();
  };

  return (
    <section
      className={
        externalMode ? "space-y-3" : "space-y-4 rounded-lg border border-border bg-surface p-3"
      }
      aria-label="Продажа упаковками, блистерами и поштучно"
    >
      {showEditor && (
        <div>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">
              {externalMode
                ? editingId
                  ? "Изменение позиции"
                  : "Добавление товара"
                : "Упаковки, блистеры и штуки"}
            </h3>
            {externalMode && catalogId && (
              <Button variant="ghost" size="sm" disabled={controlsLocked} onClick={cancelEditor}>
                Отменить
              </Button>
            )}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-foreground-muted">
            {externalMode
              ? catalogId
                ? "Выберите единицу продажи и количество."
                : "Выберите товар в поиске."
              : "Выберите формат продажи. Корзина сохраняется без списания остатков."}
          </p>
        </div>
      )}
      {items.some((item) => !item.physical_packaging) && (
        <p className="text-sm text-warning-foreground">
          В этой корзине есть позиции со старой настройкой. Здесь доступен только просмотр; новые
          форматы добавлять нельзя.
        </p>
      )}
      {showEditor && (
        <form onSubmit={(event) => void save(event)} className="space-y-3">
          <fieldset disabled={locked} className="space-y-3">
            {externalMode ? (
              <h3 className="text-base font-semibold">{selectedName || "Лекарство из каталога"}</h3>
            ) : (
              <div>
                <Label htmlFor={`${id}-medicine`}>Название лекарства</Label>
                <fieldset disabled={controlsLocked} className="border-0 p-0">
                  <CatalogPicker
                    key={pickerVersion}
                    id={`${id}-medicine`}
                    value={catalogId}
                    initialLabel={selectedName}
                    branchId={branchId}
                    showStock={false}
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
            )}
            {externalMode && !formatChosen && (
              <p role="status" className="text-sm text-foreground-muted">
                Выберите единицу продажи
              </p>
            )}
            <div role="radiogroup" aria-label="Как продавать" className="grid grid-cols-3 gap-1">
              {(externalMode
                ? (["unit", "blister", "pack"] as const)
                : (["pack", "blister", "unit"] as const)
              ).map((value) => {
                const option = options?.options.find(
                  (candidate) => candidate.sale_format === value,
                );
                return (
                  <Button
                    key={value}
                    role="radio"
                    aria-checked={formatChosen && format === value}
                    className={
                      externalMode
                        ? "pos-unit-choice h-auto min-h-16 min-w-0 px-1 py-2 text-sm"
                        : "min-w-0 px-1 text-xs"
                    }
                    variant={
                      !externalMode && formatChosen && format === value ? "primary" : "secondary"
                    }
                    disabled={controlsLocked || !option?.configured}
                    onClick={() => {
                      if (controlsLocked || !active()) return;
                      setFormatChosen(true);
                      form.setValue("format", value);
                      form.setValue("quantity", String(option?.quantity_step ?? 1));
                      setError(null);
                    }}
                  >
                    {externalMode ? (
                      <span className="flex flex-col gap-1">
                        <span>{selectionFormatNames[value]}</span>
                        <span className="text-xs font-normal">
                          {value === "unit"
                            ? 1
                            : value === "blister"
                              ? (options?.packaging?.units_per_blister ?? "—")
                              : (options?.packaging?.units_per_pack ?? "—")}{" "}
                          шт.
                        </span>
                      </span>
                    ) : (
                      formatNames[value]
                    )}
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
              <p className="text-xs leading-relaxed text-foreground-muted">
                В упаковке: {options.packaging.units_per_pack} шт.
                {options.packaging.units_per_blister
                  ? ` В блистере: ${options.packaging.units_per_blister} шт.`
                  : " Размер блистера не указан."}{" "}
                Цена выбранного варианта:{" "}
                {selectedOption?.retail_unit_price
                  ? externalMode
                    ? `${selectedOption.retail_unit_price.replace(".", ",")} сомони`
                    : `${selectedOption.retail_unit_price} TJS`
                  : "не задана"}
                .
              </p>
            )}
            <div className="grid gap-2">
              <div
                className={
                  externalMode ? "grid grid-cols-[0.7fr_1.3fr] items-end gap-2" : undefined
                }
              >
                <div className="min-w-0">
                  <Label htmlFor={`${id}-quantity`}>
                    Количество
                    {formatChosen
                      ? format === "pack"
                        ? " упаковок"
                        : format === "blister"
                          ? " блистеров"
                          : ", шт."
                      : ""}
                  </Label>
                  <Input
                    readOnly={controlsLocked}
                    disabled={!formatChosen}
                    inputMode="numeric"
                    id={`${id}-quantity`}
                    type="number"
                    min={selectedOption?.quantity_step ?? 1}
                    step={selectedOption?.quantity_step ?? 1}
                    max={1_000_000}
                    {...form.register("quantity", {
                      onChange: () => {
                        quantityEntered.current = true;
                      },
                    })}
                  />
                </div>
                {externalMode && (
                  <Button
                    variant="secondary"
                    disabled={controlsLocked || !formatChosen}
                    onClick={() => setShowNumPad(true)}
                  >
                    Цифровой ввод
                  </Button>
                )}
              </div>
              {externalMode && (
                <>
                  <p
                    aria-label="Предварительная сумма"
                    className="rounded-lg bg-surface-raised p-3 text-lg font-semibold tabular-nums"
                  >
                    {amount !== null
                      ? `${quantity} × ${selectedOption?.retail_unit_price?.replace(".", ",")} = ${amount.replace(".", ",")} сомони`
                      : "Выберите единицу и укажите количество"}
                  </p>
                  <p className="text-xs text-foreground-muted">
                    Предварительная сумма. Цена и наличие проверяются перед оплатой.
                  </p>
                </>
              )}
              <Button
                type="submit"
                className={externalMode ? "w-full" : undefined}
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
                    : externalMode
                      ? "Добавить в чек"
                      : "Добавить в корзину"}
              </Button>
              {catalogId && !externalMode && (
                <Button variant="ghost" disabled={controlsLocked} onClick={cancelEditor}>
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
      )}
      {showNumPad && !controlsLocked && formatChosen && (
        <Suspense fallback={<p role="status">Открываем цифровой ввод…</p>}>
          <NumPad
            title="Количество"
            initial={quantity}
            allowDecimal={false}
            onClose={() => setShowNumPad(false)}
            onSubmit={(value) => {
              if (controlsLocked || !active()) return;
              quantityEntered.current = true;
              form.setValue("quantity", value);
              setShowNumPad(false);
            }}
          />
        </Suspense>
      )}
      {!!items.length && (
        <ul className="divide-y divide-border">
          {items.map((item) => {
            const line = quote?.quote.lines.find((candidate) => candidate.id === item.id);
            const prepared = !item.physical_packaging;
            const step = quantityStep(rowOptions[item.catalog_id], item.sale_format);
            const stepperLocked =
              rowControlsLocked ||
              prepared ||
              !matchesRowOptions(rowOptions[item.catalog_id], item, saleId, branchId) ||
              !step ||
              item.quantity % step !== 0;
            return (
              <li key={item.id} className="space-y-2 py-3">
                <div
                  className={
                    externalMode
                      ? "grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3"
                      : "min-w-0"
                  }
                >
                  <p className="break-words text-sm font-semibold">
                    {nameById[item.catalog_id] ?? "Лекарство из каталога"}
                  </p>
                  {externalMode && (
                    <p className="whitespace-nowrap text-right text-base font-semibold tabular-nums">
                      {line ? `${line.net_amount.replace(".", ",")} сомони` : "Не рассчитано"}
                    </p>
                  )}
                  <p
                    className={
                      externalMode
                        ? "col-span-2 mt-1 text-xs text-foreground-muted"
                        : "mt-1 text-xs text-foreground-muted"
                    }
                  >
                    {formatNames[item.sale_format]} · {item.quantity}
                    {!externalMode && <> · {line ? `${line.net_amount} TJS` : "Не рассчитано"}</>}
                  </p>
                  {prepared && (
                    <p
                      className={
                        externalMode ? "col-span-2 text-sm text-warning" : "text-sm text-warning"
                      }
                    >
                      Старая настройка поштучной продажи. Эту строку пока нельзя изменять здесь.
                    </p>
                  )}
                </div>
                <div
                  className={
                    externalMode
                      ? "flex flex-wrap items-center justify-start gap-1"
                      : "flex flex-wrap items-center justify-end gap-1"
                  }
                >
                  {externalMode && (
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label="Уменьшить количество"
                      disabled={stepperLocked || item.quantity - (step ?? 1) < (step ?? 1)}
                      onClick={() => {
                        if (!stepperLocked) void changeRowQuantity(item, -1);
                      }}
                    >
                      −
                    </Button>
                  )}
                  <Button
                    variant={externalMode ? "secondary" : "ghost"}
                    size="sm"
                    aria-label={externalMode ? "Изменить" : undefined}
                    title={externalMode ? "Изменить количество" : undefined}
                    disabled={rowControlsLocked || prepared}
                    onClick={() => {
                      if (rowControlsLocked || !active()) return;
                      itemId.current = item.id;
                      if (!editing.current) {
                        editing.current = true;
                        latest.current.onEditingChange?.(true);
                      }
                      externalEntry.current = false;
                      setFormatChosen(true);
                      setShowNumPad(false);
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
                    {externalMode ? item.quantity : "Изменить"}
                  </Button>
                  {externalMode && (
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label="Увеличить количество"
                      disabled={stepperLocked || item.quantity + (step ?? 1) > 1_000_000}
                      onClick={() => {
                        if (!stepperLocked) void changeRowQuantity(item, 1);
                      }}
                    >
                      +
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={rowControlsLocked || prepared}
                    onClick={() => {
                      if (!rowControlsLocked) void remove(item);
                    }}
                  >
                    {externalMode ? "Убрать" : "Удалить"}
                  </Button>
                </div>
                {externalMode && rowOptions[item.catalog_id] === null && (
                  <p className="text-xs text-foreground-muted">
                    Не удалось проверить шаг количества. Используйте «Изменить».
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
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
      {!!items.length && !props.hideTotal && (
        <p className="border-t border-border pt-3 text-right font-semibold tabular-nums">
          Итого:{" "}
          {quote
            ? externalMode
              ? `${quote.quote.total_amount.replace(".", ",")} сомони`
              : `${quote.quote.total_amount} TJS`
            : "Не рассчитано"}
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
