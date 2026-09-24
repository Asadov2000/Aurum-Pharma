import { useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import {
  barcodeLabel,
  barcodeOptions,
  dispensingLabel,
  dispensingOptions,
  storageLabel,
  storageOptions,
} from "./labels";
import { useCatalogItemQuery, useUpdateCatalogItem } from "./queries";
import { listCatalog } from "./api";
import { useCatalogCreate } from "./useCatalogCreate";
import { type CatalogItem } from "./types";

const optionalText = z.string().trim().max(255, "Не более 255 символов").optional();

const schema = z
  .object({
    brand_name: z.string().trim().min(1, "Введите название").max(255, "Не более 255 символов"),
    inn: optionalText,
    manufacturer: optionalText,
    form: optionalText,
    dosage: optionalText,
    pack_size: optionalText,
    atx_code: z.string().trim().max(32, "Не более 32 символов").optional(),
    dispensing_type: z
      .enum(["", "prescription", "otc", "special"])
      .refine((value) => value !== "", "Выберите условия отпуска"),
    storage_type: z
      .enum(["", "normal", "cold", "frozen"])
      .refine((value) => value !== "", "Выберите условия хранения"),
    category: optionalText,
    base_price: z
      .string()
      .trim()
      .refine(
        (value) => value === "" || /^(?:0|[1-9]\d{0,11})(?:[.,]\d{1,2})?$/.test(value),
        "Введите неотрицательную сумму, не более 2 знаков после запятой",
      )
      .optional(),
    is_active: z.boolean(),
    initial_barcode: z.string().trim().max(200, "Не более 200 символов"),
    barcode_type: z.enum(["", "ean13", "ean8", "gs1_128", "code128", "qr", "other"]),
  })
  .superRefine((values, context) => {
    if (values.initial_barcode && !values.barcode_type) {
      context.addIssue({
        code: "custom",
        path: ["barcode_type"],
        message: "Выберите тип штрихкода",
      });
    }
  });

type ParsedValues = z.infer<typeof schema>;
type FormValues = Omit<ParsedValues, "dispensing_type" | "storage_type"> & {
  dispensing_type: ParsedValues["dispensing_type"] | "";
  storage_type: ParsedValues["storage_type"] | "";
};

interface Props {
  item: CatalogItem | null;
  onClose: () => void;
  onCancel?: () => void;
  initialName?: string;
  onCreated?: (item: CatalogItem) => void;
  onSelectedExisting?: (item: CatalogItem) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

export function CatalogItemForm({
  item,
  onClose,
  onCancel = onClose,
  initialName = "",
  onCreated,
  onSelectedExisting,
  onDirtyChange,
}: Props): JSX.Element {
  const isEdit = item !== null;
  const create = useCatalogCreate();
  const update = useUpdateCatalogItem();
  const [topError, setTopError] = useState<string | null>(null);
  const [exactMatchId, setExactMatchId] = useState<string | null>(null);
  const [reviewedIdentity, setReviewedIdentity] = useState<string | null>(null);
  const identityAtConflict = useRef<string | null>(null);
  const exactMatch = useCatalogItemQuery(exactMatchId);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const form = useForm<FormValues>({
    defaultValues: {
      brand_name: item?.brand_name ?? initialName,
      inn: item?.inn ?? "",
      manufacturer: item?.manufacturer ?? "",
      form: item?.form ?? "",
      dosage: item?.dosage ?? "",
      pack_size: item?.pack_size ?? "",
      atx_code: item?.atx_code ?? "",
      dispensing_type: item?.dispensing_type ?? "",
      storage_type: item?.storage_type ?? "",
      category: item?.category ?? "",
      base_price: item?.base_price ?? "",
      is_active: item?.is_active ?? true,
      initial_barcode: "",
      barcode_type: "",
    },
  });
  const brandName = form.watch("brand_name").trim();
  const identityFields = form.watch(["brand_name", "manufacturer", "form", "dosage", "pack_size"]);
  const identityKey = identityFields.join("\u0000");
  useEffect(() => {
    if (exactMatchId && identityAtConflict.current !== identityKey) setExactMatchId(null);
  }, [exactMatchId, identityKey]);
  const [similarName, setSimilarName] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSimilarName(brandName), 200);
    return () => clearTimeout(timer);
  }, [brandName]);
  const similar = useQuery({
    queryKey: ["catalog", "creation-similar", similarName],
    queryFn: ({ signal }) => listCatalog({ q: similarName, page: 1, page_size: 5 }, signal),
    enabled: !isEdit && Boolean(onSelectedExisting) && similarName.length >= 2 && !create.uncertain,
    staleTime: 30_000,
  });
  const searchReady =
    isEdit ||
    !onSelectedExisting ||
    create.uncertain ||
    brandName.length < 2 ||
    (similarName === brandName && similar.isError) ||
    (similarName === brandName &&
      !similar.isFetching &&
      !similar.isPlaceholderData &&
      !similar.isError &&
      Boolean(similar.data));

  useEffect(() => {
    form.reset({
      brand_name: item?.brand_name ?? initialName,
      inn: item?.inn ?? "",
      manufacturer: item?.manufacturer ?? "",
      form: item?.form ?? "",
      dosage: item?.dosage ?? "",
      pack_size: item?.pack_size ?? "",
      atx_code: item?.atx_code ?? "",
      dispensing_type: item?.dispensing_type ?? "",
      storage_type: item?.storage_type ?? "",
      category: item?.category ?? "",
      base_price: item?.base_price ?? "",
      is_active: item?.is_active ?? true,
      initial_barcode: "",
      barcode_type: "",
    });
  }, [item, form, initialName]);

  useEffect(() => {
    onDirtyChange?.(
      form.formState.isDirty || form.formState.isSubmitting || (!isEdit && create.uncertain),
    );
  }, [
    form.formState.isDirty,
    form.formState.isSubmitting,
    isEdit,
    create.uncertain,
    onDirtyChange,
  ]);

  const submitValues = async (values: FormValues, confirmDistinct = false) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      const firstIssue = parsed.error.issues[0]?.path[0];
      if (typeof firstIssue === "string") form.setFocus(firstIssue as keyof FormValues);
      return;
    }
    setTopError(null);
    if (!searchReady && !confirmDistinct) return;
    if (
      !confirmDistinct &&
      !isEdit &&
      onSelectedExisting &&
      similarName === brandName &&
      !similar.isFetching &&
      !similar.isPlaceholderData &&
      !similar.isError &&
      similar.data?.items.length &&
      reviewedIdentity !== identityKey
    ) {
      setReviewedIdentity(identityKey);
      return;
    }
    setExactMatchId(null);
    const d = parsed.data;
    if (!d.dispensing_type || !d.storage_type) return;
    const nullable = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);
    try {
      if (isEdit && item) {
        await update.mutateAsync({
          id: item.id,
          payload: {
            brand_name: d.brand_name.trim(),
            inn: nullable(d.inn),
            manufacturer: nullable(d.manufacturer),
            form: nullable(d.form),
            dosage: nullable(d.dosage),
            pack_size: nullable(d.pack_size),
            atx_code: nullable(d.atx_code),
            dispensing_type: d.dispensing_type,
            storage_type: d.storage_type,
            category: nullable(d.category),
            base_price: nullable(d.base_price)?.replace(",", ".") ?? null,
            is_active: d.is_active,
          },
        });
      } else {
        const created = await create.submit({
          ...(confirmDistinct ? { confirm_distinct_item: true } : {}),
          brand_name: d.brand_name.trim(),
          inn: nullable(d.inn),
          manufacturer: nullable(d.manufacturer),
          form: nullable(d.form),
          dosage: nullable(d.dosage),
          pack_size: nullable(d.pack_size),
          atx_code: nullable(d.atx_code),
          dispensing_type: d.dispensing_type,
          storage_type: d.storage_type,
          category: nullable(d.category),
          base_price: nullable(d.base_price)?.replace(",", ".") ?? null,
          ...(d.initial_barcode && d.barcode_type
            ? { initial_barcode: { code: d.initial_barcode, code_type: d.barcode_type } }
            : {}),
        });
        if (!mounted.current) return;
        onCreated?.(created);
      }
      if (!mounted.current) return;
      onClose();
    } catch (err) {
      if (!mounted.current) return;
      const parsedError = isAxiosError(err)
        ? z
            .object({ error: z.object({ details: z.object({ reason: z.string() }) }) })
            .safeParse(err.response?.data)
        : null;
      const reason = parsedError?.success ? parsedError.data.error.details.reason : undefined;
      if (reason === "catalog_identity_match" && isAxiosError(err)) {
        const duplicate = z
          .object({
            error: z.object({ details: z.object({ catalog_id: z.string().uuid() }) }),
          })
          .safeParse(err.response?.data);
        if (duplicate.success) {
          identityAtConflict.current = identityKey;
          setExactMatchId(duplicate.data.error.details.catalog_id);
        }
      }
      setTopError(
        reason === "barcode_conflict"
          ? "Этот штрихкод уже принадлежит другому товару. Проверьте код или выберите существующий товар."
          : reason === "catalog_identity_match"
            ? "Такая карточка уже есть в каталоге. Выберите её или явно подтвердите создание отдельного товара."
            : reason === "operation_payload_mismatch"
              ? "Эта операция уже использована с другими данными. Закройте карточку и проверьте каталог перед новым созданием."
              : reason === "created_item_unavailable"
                ? "Ранее созданный товар архивирован или выключен. Проверьте его карточку в каталоге."
                : describeApiError(err, "Не удалось сохранить позицию"),
      );
    }
  };
  const onSubmit = form.handleSubmit((values) =>
    submitValues(
      values,
      !isEdit && reviewedIdentity === identityKey && Boolean(similar.data?.items.length),
    ),
  );

  return (
    <form
      onSubmit={(event) => {
        event.stopPropagation();
        void onSubmit(event);
      }}
      noValidate
      className="space-y-4"
    >
      {!isEdit && create.uncertain && (
        <p
          role="status"
          className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm"
        >
          Ответ не получен. Повторите запрос с теми же данными, чтобы проверить создание товара.
        </p>
      )}
      <fieldset disabled={!isEdit && (create.pending || create.uncertain)}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="brand_name">Торговое название</Label>
            <Input
              id="brand_name"
              invalid={Boolean(form.formState.errors.brand_name)}
              autoComplete="off"
              {...form.register("brand_name")}
            />
            <FormError>{form.formState.errors.brand_name?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="inn">МНН (действующее вещество)</Label>
            <Input id="inn" autoComplete="off" {...form.register("inn")} />
          </div>
          <div>
            <Label htmlFor="manufacturer">Производитель</Label>
            <Input id="manufacturer" autoComplete="off" {...form.register("manufacturer")} />
          </div>
          <div>
            <Label htmlFor="form">Форма выпуска</Label>
            <Input id="form" placeholder="таблетки, сироп…" {...form.register("form")} />
          </div>
          <div>
            <Label htmlFor="dosage">Дозировка</Label>
            <Input id="dosage" placeholder="500 мг" {...form.register("dosage")} />
          </div>
          <div>
            <Label htmlFor="pack_size">Размер упаковки</Label>
            <Input id="pack_size" placeholder="20 таблеток" {...form.register("pack_size")} />
          </div>
          <div>
            <Label htmlFor="atx_code">ATX-код</Label>
            <Input id="atx_code" {...form.register("atx_code")} />
          </div>
          <div>
            <Label htmlFor="dispensing_type">Условия отпуска</Label>
            <Select
              id="dispensing_type"
              invalid={Boolean(form.formState.errors.dispensing_type)}
              {...form.register("dispensing_type")}
            >
              <option value="">Выберите условия</option>
              {dispensingOptions.map((d) => (
                <option key={d} value={d}>
                  {dispensingLabel[d]}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.dispensing_type?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="storage_type">Условия хранения</Label>
            <Select
              id="storage_type"
              invalid={Boolean(form.formState.errors.storage_type)}
              {...form.register("storage_type")}
            >
              <option value="">Выберите условия</option>
              {storageOptions.map((s) => (
                <option key={s} value={s}>
                  {storageLabel[s]}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.storage_type?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="category">Категория</Label>
            <Input id="category" {...form.register("category")} />
          </div>
          <div>
            <Label htmlFor="base_price">Цена по умолчанию (TJS)</Label>
            <Input
              id="base_price"
              type="text"
              inputMode="decimal"
              placeholder="0.00"
              invalid={Boolean(form.formState.errors.base_price)}
              {...form.register("base_price")}
            />
            <FormError>{form.formState.errors.base_price?.message}</FormError>
          </div>
          {isEdit && (
            <div className="sm:col-span-2">
              <Switch
                label="Показывать товар в кассе и рабочих списках"
                {...form.register("is_active")}
              />
              <p className="mt-1 text-xs text-foreground-muted">
                Отключите, если товар временно нельзя выбирать для новых операций.
              </p>
            </div>
          )}
          {!isEdit && (
            <>
              <div>
                <Label htmlFor="catalog-initial-barcode">Первый штрихкод (необязательно)</Label>
                <Input
                  id="catalog-initial-barcode"
                  maxLength={200}
                  autoComplete="off"
                  {...form.register("initial_barcode")}
                />
                <FormError>{form.formState.errors.initial_barcode?.message}</FormError>
              </div>
              <div>
                <Label htmlFor="catalog-initial-barcode-type">Тип штрихкода</Label>
                <Select
                  id="catalog-initial-barcode-type"
                  invalid={Boolean(form.formState.errors.barcode_type)}
                  {...form.register("barcode_type")}
                >
                  <option value="">Выберите тип</option>
                  {barcodeOptions.map((type) => (
                    <option key={type} value={type}>
                      {barcodeLabel[type]}
                    </option>
                  ))}
                </Select>
                <FormError>{form.formState.errors.barcode_type?.message}</FormError>
              </div>
            </>
          )}
        </div>
      </fieldset>
      {!isEdit && onSelectedExisting && similarName.length >= 2 && !create.uncertain && (
        <div className="rounded-lg border border-border bg-surface-subtle p-3 text-sm">
          <p className="font-medium">Проверьте, нет ли товара в каталоге</p>
          {similar.isFetching || similar.isPlaceholderData || similarName !== brandName ? (
            <p role="status" className="mt-2 text-foreground-muted">
              Ищем похожие товары…
            </p>
          ) : similar.isError ? (
            <div className="mt-2 space-y-2">
              <p role="alert" className="text-danger">
                Не удалось проверить каталог. Можно повторить поиск или продолжить: сервер всё равно
                проверит точное совпадение перед созданием.
              </p>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => void similar.refetch()}
              >
                Повторить поиск
              </Button>
            </div>
          ) : similar.data?.items.length ? (
            <ul className="mt-2 space-y-1">
              {similar.data.items.map((candidate) => (
                <li key={candidate.id}>
                  <div className="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-left">
                    <span className="block font-medium">{candidate.brand_name}</span>
                    <span className="text-xs text-foreground-muted">
                      {[candidate.dosage, candidate.pack_size, candidate.manufacturer]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                    <button
                      type="button"
                      className="mt-1 font-medium text-primary underline"
                      onClick={() => {
                        onSelectedExisting(candidate);
                        onClose();
                      }}
                    >
                      Выбрать этот товар
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-foreground-muted">
              Похожих товаров не найдено. Проверьте дозировку и упаковку.
            </p>
          )}
        </div>
      )}
      {!isEdit &&
      onSelectedExisting &&
      reviewedIdentity === identityKey &&
      similar.data?.items.length ? (
        <p role="status" className="text-sm text-warning-foreground">
          Проверьте дозировку, упаковку и производителя. Если это другой товар, подтвердите создание
          ниже.
        </p>
      ) : null}
      {exactMatchId && !isEdit && (
        <div className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm">
          <p className="font-medium">Найдено точное совпадение</p>
          {exactMatch.isLoading ? (
            <p role="status">Загружается карточка…</p>
          ) : exactMatch.isError ? (
            <div className="mt-2 space-y-2">
              <p role="alert">
                Не удалось загрузить найденную карточку. Проверьте право просмотра каталога или
                повторите загрузку.
              </p>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => void exactMatch.refetch()}
              >
                Повторить загрузку
              </Button>
            </div>
          ) : exactMatch.data ? (
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span>
                {[
                  exactMatch.data.brand_name,
                  exactMatch.data.dosage,
                  exactMatch.data.pack_size,
                  exactMatch.data.manufacturer,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {onSelectedExisting && (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    onSelectedExisting(exactMatch.data);
                    onClose();
                  }}
                >
                  Выбрать существующий товар
                </Button>
              )}
              {!onSelectedExisting && (
                <a
                  href="/catalog"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary underline"
                >
                  Открыть каталог
                </a>
              )}
            </div>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="mt-2"
            disabled={form.formState.isSubmitting || !exactMatch.data}
            onClick={() => void form.handleSubmit((values) => submitValues(values, true))()}
          >
            Создать отдельный товар
          </Button>
        </div>
      )}
      {topError && (
        <p className="text-sm text-danger" role="alert">
          {topError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting} disabled={!searchReady}>
          {isEdit
            ? "Сохранить изменения"
            : create.uncertain
              ? "Повторить создание"
              : reviewedIdentity === identityKey && similar.data?.items.length
                ? "Проверил совпадения, создать отдельный товар"
                : "Добавить товар"}
        </Button>
      </div>
    </form>
  );
}
