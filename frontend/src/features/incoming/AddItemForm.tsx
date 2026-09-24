import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Button, ConfirmDialog, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { createOperationId } from "@/lib/operationId";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";

import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { type CatalogItem } from "@/features/catalog/types";
import { pharmacyCalendarDate } from "./calendar";
import { useUpdateIncomingItem } from "./queries";
import { type IncomingDraftFields, type IncomingDraftRow, type IncomingItem } from "./types";
import { draftFields, useIncomingDraft } from "./useIncomingDraft";

const CatalogCreateDialog = lazy(() =>
  import("@/features/catalog/CatalogCreateDialog").then((module) => ({
    default: module.CatalogCreateDialog,
  })),
);

const decimalString = (
  emptyMessage: string,
  invalidMessage: string,
  allowZero: boolean,
  maxIntegerDigits: number,
  maxDecimalPlaces: number,
) =>
  z
    .string()
    .trim()
    .min(1, emptyMessage)
    .refine((value) => {
      const normalized = value.replace(",", ".");
      if (!/^\d+(?:\.\d+)?$/.test(normalized)) return false;
      const [integer = "", fraction = ""] = normalized.split(".");
      const significantInteger = integer.replace(/^0+(?=\d)/, "");
      const number = Number(normalized);
      return (
        significantInteger.length <= maxIntegerDigits &&
        fraction.length <= maxDecimalPlaces &&
        Number.isFinite(number) &&
        (allowZero ? number >= 0 : number > 0)
      );
    }, invalidMessage);

const schema = z
  .object({
    source_name: z.string().optional(),
    catalog_id: z.string().min(1, "Выберите позицию"),
    batch_number: z.string().optional(),
    manufactured_at: z.string().optional(),
    expires_at: z.string().min(1, "Укажите срок годности"),
    qty: decimalString(
      "Укажите количество",
      "Количество: до 11 цифр и 3 знаков после запятой, больше 0",
      false,
      11,
      3,
    ),
    purchase_price: decimalString(
      "Укажите цену закупки",
      "Цена: до 12 цифр и 2 знаков после запятой",
      true,
      12,
      2,
    ),
    sale_price: decimalString(
      "Укажите цену продажи",
      "Цена: до 12 цифр и 2 знаков после запятой",
      true,
      12,
      2,
    ),
  })
  .superRefine((values, context) => {
    if (values.expires_at && values.expires_at <= pharmacyCalendarDate()) {
      context.addIssue({
        code: "custom",
        path: ["expires_at"],
        message: "Срок годности должен быть позже сегодняшней даты",
      });
    }
    if (values.manufactured_at && values.expires_at && values.manufactured_at > values.expires_at) {
      context.addIssue({
        code: "custom",
        path: ["manufactured_at"],
        message: "Дата производства не может быть позже срока годности",
      });
    }
  });

type FormValues = z.infer<typeof schema>;

function draftSnapshot(values: Partial<FormValues>): IncomingDraftFields {
  return {
    source_name: values.source_name ?? "",
    catalog_id: values.catalog_id ?? "",
    batch_number: values.batch_number ?? "",
    manufactured_at: values.manufactured_at ?? "",
    expires_at: values.expires_at ?? "",
    qty: values.qty ?? "",
    purchase_price: values.purchase_price ?? "",
    sale_price: values.sale_price ?? "",
  };
}

export function AddItemForm({
  documentId,
  onClose,
  onCancel = onClose,
  onDirtyChange,
  item,
  draftRow,
}: {
  documentId: string;
  onClose: () => void;
  onCancel?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  item?: IncomingItem;
  draftRow?: IncomingDraftRow;
}): JSX.Element {
  const updateItem = useUpdateIncomingItem();
  const [topError, setTopError] = useState<string | null>(null);
  const [operationId] = useState(createOperationId);
  const isEditing = item !== undefined;
  const { user } = useAuth();
  const canCreateCatalog = hasPermission(user, "catalog.create");
  const [catalogSearch, setCatalogSearch] = useState(draftRow?.fields.source_name ?? "");
  const [catalogCreateName, setCatalogCreateName] = useState("");
  const [catalogCreateOpen, setCatalogCreateOpen] = useState(false);
  const [catalogCreateDirty, setCatalogCreateDirty] = useState(false);
  const [preparingCatalog, setPreparingCatalog] = useState(false);
  const draft = useIncomingDraft(documentId, draftRow, !isEditing);
  const [reloadOpen, setReloadOpen] = useState(false);
  const [reloadPending, setReloadPending] = useState(false);
  const [catalogLabel, setCatalogLabel] = useState(draftRow?.catalog_name ?? undefined);
  const [pickerVersion, setPickerVersion] = useState(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const form = useForm<FormValues>({
    defaultValues: item
      ? {
          catalog_id: item?.catalog_id ?? "",
          batch_number: item?.batch_number ?? "",
          manufactured_at: item?.manufactured_at ?? "",
          expires_at: item?.expires_at ?? "",
          qty: item?.qty ?? "",
          purchase_price: item?.purchase_price ?? "",
          sale_price: item?.sale_price ?? "",
        }
      : draftFields(draftRow),
  });

  useEffect(() => {
    onDirtyChange?.(
      isEditing ? form.formState.isDirty : draft.unsaved || catalogCreateDirty || preparingCatalog,
    );
  }, [
    form.formState.isDirty,
    onDirtyChange,
    isEditing,
    draft.unsaved,
    catalogCreateDirty,
    preparingCatalog,
  ]);

  const updateDraft = draft.update;
  useEffect(() => {
    if (isEditing) return;
    const subscription = form.watch((values) => updateDraft(draftSnapshot(values)));
    return () => subscription.unsubscribe();
  }, [form, isEditing, updateDraft]);

  const openCatalogCreate = async (searchName = catalogSearch) => {
    if (
      !canCreateCatalog ||
      preparingCatalog ||
      catalogCreateOpen ||
      draft.commitPending ||
      draft.commitUncertain
    )
      return;
    setPreparingCatalog(true);
    setTopError(null);
    try {
      await draft.save();
      if (!mounted.current) return;
      setCatalogCreateName(searchName.trim());
      setCatalogCreateOpen(true);
    } catch {
      if (mounted.current)
        setTopError(
          "Сначала сохраните строку прихода. Повторите сохранение, затем откройте карточку товара.",
        );
    } finally {
      if (mounted.current) setPreparingCatalog(false);
    }
  };

  const selectCatalogFromDialog = (selected: CatalogItem) => {
    form.setValue("catalog_id", selected.id, { shouldDirty: true });
    // Do not depend on the form watch callback running before the immediate
    // save when a catalog dialog selects an existing card.
    updateDraft(draftSnapshot({ ...form.getValues(), catalog_id: selected.id }));
    form.clearErrors("catalog_id");
    setCatalogLabel(selected.brand_name);
    setPickerVersion((version) => version + 1);
    setTopError(null);
    void draft.save().catch(() => {
      if (mounted.current)
        setTopError(
          "Товар выбран. Связь со строкой прихода ещё не сохранена — повторите сохранение черновика.",
        );
    });
  };

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      let firstInvalidField: keyof FormValues | null = null;
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        const field = p as keyof FormValues;
        firstInvalidField ??= field;
        form.setError(field, { message: issue.message });
      }
      if (firstInvalidField) form.setFocus(firstInvalidField);
      return;
    }
    setTopError(null);
    const d = parsed.data;
    try {
      const payload = {
        operation_id: operationId,
        catalog_id: d.catalog_id,
        batch_number: d.batch_number?.trim() || null,
        manufactured_at: d.manufactured_at || null,
        expires_at: d.expires_at,
        qty: d.qty.replace(",", "."),
        purchase_price: d.purchase_price.replace(",", "."),
        sale_price: d.sale_price.replace(",", "."),
      };
      if (item) {
        await updateItem.mutateAsync({
          documentId,
          itemId: item.id,
          payload: {
            catalog_id: payload.catalog_id,
            batch_number: payload.batch_number,
            manufactured_at: payload.manufactured_at,
            expires_at: payload.expires_at,
            qty: payload.qty,
            purchase_price: payload.purchase_price,
            sale_price: payload.sale_price,
          },
        });
      } else {
        await draft.commit();
      }
      if (!mounted.current) return;
      onDirtyChange?.(false);
      onClose();
    } catch (err) {
      setTopError(
        describeApiError(
          err,
          isEditing ? "Не удалось сохранить позицию" : "Не удалось добавить позицию",
        ),
      );
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-3">
      {!isEditing && (
        <div className="space-y-2 rounded-lg border border-border bg-surface-subtle p-3 text-sm">
          <p role="status" aria-live="polite">
            {draft.commitUncertain
              ? "Ответ не получен. Повторите добавление, чтобы проверить результат."
              : draft.phase === "saved"
                ? "Сохранено в черновике"
                : draft.phase === "saving"
                  ? "Сохраняется…"
                  : draft.phase === "error"
                    ? "Не сохранено. Проверьте соединение и повторите."
                    : draft.phase === "conflict"
                      ? "Черновик изменён на другом устройстве или уже закрыт. Ваш ввод остаётся в форме."
                      : draft.phase === "unsaved"
                        ? "Изменения ещё не сохранены"
                        : "Ввод будет автоматически сохранён в черновике"}
          </p>
          {draft.phase === "error" && !draft.commitUncertain && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void draft.save().catch(() => undefined)}
            >
              Повторить сохранение
            </Button>
          )}
          {draft.phase === "conflict" && (
            <Button type="button" size="sm" variant="secondary" onClick={() => setReloadOpen(true)}>
              Загрузить сохранённое
            </Button>
          )}
        </div>
      )}
      <fieldset
        disabled={
          reloadPending ||
          preparingCatalog ||
          catalogCreateOpen ||
          (!isEditing && (draft.commitPending || draft.commitUncertain))
        }
        className="space-y-3"
      >
        <input type="hidden" {...form.register("source_name")} />
        {draftRow?.fields.source_name && (
          <p className="rounded-md border border-border bg-surface-subtle px-3 py-2 text-sm">
            В накладной: <strong>{draftRow.fields.source_name}</strong>
          </p>
        )}
        <div>
          <Label htmlFor="incoming-catalog-item">Лекарство или товар</Label>
          <Controller
            control={form.control}
            name="catalog_id"
            render={({ field }) => (
              <CatalogPicker
                key={pickerVersion}
                id="incoming-catalog-item"
                value={field.value}
                initialLabel={item?.catalog_name ?? catalogLabel}
                initialSearch={
                  draftRow?.fields.catalog_id ? undefined : draftRow?.fields.source_name
                }
                onSearchChange={setCatalogSearch}
                onChange={(id, name) => {
                  setCatalogLabel(name);
                  field.onChange(id);
                }}
                invalid={Boolean(form.formState.errors.catalog_id)}
              />
            )}
          />
          <FormError>{form.formState.errors.catalog_id?.message}</FormError>
          {!isEditing && !canCreateCatalog && catalogSearch.trim().length >= 2 && (
            <p className="text-xs text-foreground-muted">
              Не нашли товар? Попросите сотрудника с правом создания товаров добавить его в каталог.
            </p>
          )}
          {!isEditing && canCreateCatalog && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="mt-2 min-h-11"
              isLoading={preparingCatalog}
              onClick={() => void openCatalogCreate()}
            >
              {catalogSearch.trim().length >= 2
                ? `Проверить и создать «${catalogSearch.trim()}»`
                : "Новый товар"}
            </Button>
          )}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="batch_number">Номер партии</Label>
            <Input
              id="batch_number"
              maxLength={isEditing ? undefined : 120}
              {...form.register("batch_number")}
            />
          </div>
          <div>
            <Label htmlFor="qty">Количество единиц</Label>
            <Input
              id="qty"
              type="text"
              inputMode="decimal"
              maxLength={isEditing ? undefined : 64}
              invalid={Boolean(form.formState.errors.qty)}
              {...form.register("qty")}
            />
            <FormError>{form.formState.errors.qty?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="manufactured_at">Дата производства</Label>
            <Input id="manufactured_at" type="date" {...form.register("manufactured_at")} />
            <FormError>{form.formState.errors.manufactured_at?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="expires_at">Срок годности</Label>
            <Input
              id="expires_at"
              type="date"
              invalid={Boolean(form.formState.errors.expires_at)}
              {...form.register("expires_at")}
            />
            <FormError>{form.formState.errors.expires_at?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="purchase_price">Закупочная цена за единицу</Label>
            <Input
              id="purchase_price"
              type="text"
              inputMode="decimal"
              maxLength={isEditing ? undefined : 64}
              invalid={Boolean(form.formState.errors.purchase_price)}
              {...form.register("purchase_price")}
            />
            <FormError>{form.formState.errors.purchase_price?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="sale_price">Розничная цена за единицу</Label>
            <Input
              id="sale_price"
              type="text"
              inputMode="decimal"
              maxLength={isEditing ? undefined : 64}
              invalid={Boolean(form.formState.errors.sale_price)}
              {...form.register("sale_price")}
            />
            <FormError>{form.formState.errors.sale_price?.message}</FormError>
          </div>
        </div>
      </fieldset>
      {topError && (
        <p className="text-sm text-danger" role="alert">
          {topError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {isEditing ? "Отмена" : "Закрыть"}
        </Button>
        <Button
          type="submit"
          size="sm"
          isLoading={form.formState.isSubmitting}
          disabled={
            preparingCatalog ||
            catalogCreateOpen ||
            reloadPending ||
            (!isEditing && draft.phase === "conflict")
          }
        >
          {isEditing ? "Сохранить" : draft.commitUncertain ? "Повторить добавление" : "Добавить"}
        </Button>
      </div>
      <ConfirmDialog
        open={reloadOpen}
        title="Загрузить сохранённый черновик?"
        message="Текущие изменения в этой форме будут заменены сохранённой серверной версией."
        confirmLabel="Загрузить сохранённое"
        isLoading={reloadPending}
        cancelDisabled={reloadPending}
        onCancel={() => {
          if (!reloadPending) setReloadOpen(false);
        }}
        onConfirm={() => {
          if (reloadPending) return;
          setReloadPending(true);
          void draft
            .reload()
            .then((current) => {
              if (!mounted.current) return;
              form.reset(draftFields(current));
              setCatalogLabel(current.catalog_name ?? undefined);
              setCatalogSearch(current.fields.source_name ?? "");
              setPickerVersion((version) => version + 1);
              setReloadOpen(false);
              setTopError(null);
            })
            .catch(() => {
              if (!mounted.current) return;
              setReloadOpen(false);
              setTopError(
                "Не удалось загрузить черновик. Возможно, позиция уже добавлена или удалена. Ваш ввод остаётся в форме.",
              );
            })
            .finally(() => {
              if (mounted.current) setReloadPending(false);
            });
        }}
      />
      {catalogCreateOpen && (
        <Suspense fallback={<p role="status">Открывается карточка товара…</p>}>
          <CatalogCreateDialog
            open={catalogCreateOpen}
            initialName={catalogCreateName}
            guardNavigation={false}
            onDirtyChange={setCatalogCreateDirty}
            onClose={() => {
              setCatalogCreateOpen(false);
              setCatalogCreateDirty(false);
            }}
            onCreated={selectCatalogFromDialog}
            onSelectedExisting={selectCatalogFromDialog}
          />
        </Suspense>
      )}
    </form>
  );
}
