import { useEffect, useId, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { useConnectivity } from "@/lib/connectivityContext";
import { cn } from "@/lib/utils";

import {
  formatSupplierDate,
  formatSupplierMoney,
  formatSupplierQuantity,
  supplierProductSubtitle,
} from "./formatters";
import { supplierReturnReasonLabel, supplierReturnReasonOptions } from "./labels";
import { useCreateSupplierReturn, useSupplierReturnCandidatesQuery } from "./queries";
import {
  type Supplier,
  type SupplierReturnCandidate,
  type SupplierReturnCreated,
  type SupplierReturnReason,
} from "./types";

const quantityPattern = /^(?:0|[1-9]\d{0,10})(?:[.,]\d{1,3})?$/;

const schema = z.object({
  batch_id: z.string().min(1, "Выберите партию"),
  source_document_id: z.string().min(1, "Не найден исходный приход"),
  qty: z
    .string()
    .trim()
    .min(1, "Введите количество")
    .regex(quantityPattern, "До 11 цифр и не более 3 знаков после запятой")
    .refine((value) => Number(value.replace(",", ".")) > 0, "Количество должно быть больше 0"),
  reason: z.enum(["damaged", "expired", "incorrect_delivery", "quality_issue", "other"], {
    required_error: "Выберите причину",
  }),
  comment: z.string().max(2000, "Не более 2000 символов").optional(),
});

interface FormValues {
  batch_id: string;
  source_document_id: string;
  qty: string;
  reason: SupplierReturnReason | "";
  comment: string;
}

function createOperationId(): string {
  if (typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function SupplierReturnForm({
  supplier,
  onClose,
  onDirtyChange,
  allowedBranchIds = null,
}: {
  supplier: Supplier;
  onClose: () => void;
  onDirtyChange: (dirty: boolean) => void;
  allowedBranchIds?: readonly string[] | null;
}): JSX.Element {
  const operationId = useMemo(createOperationId, []);
  const createReturn = useCreateSupplierReturn();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [branchId, setBranchId] = useState("");
  const [selected, setSelected] = useState<SupplierReturnCandidate | null>(null);
  const [createdReturn, setCreatedReturn] = useState<SupplierReturnCreated | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  const online = useConnectivity().canUseServer;
  const [highlight, setHighlight] = useState(0);
  const candidateListId = useId();

  const form = useForm<FormValues>({
    defaultValues: {
      batch_id: "",
      source_document_id: "",
      qty: "",
      reason: "",
      comment: "",
    },
  });
  const branches = useBranchesQuery(false);
  const availableBranches = useMemo(() => {
    const items = branches.data ?? [];
    if (allowedBranchIds === null) return items;
    const allowed = new Set(allowedBranchIds);
    return items.filter((branch) => allowed.has(branch.id));
  }, [allowedBranchIds, branches.data]);
  const candidates = useSupplierReturnCandidatesQuery(
    {
      supplier_id: supplier.id,
      branch_id: branchId || undefined,
      q: search || undefined,
      page: 1,
      page_size: 20,
    },
    Boolean(branchId),
  );
  const qty = form.watch("qty").replace(",", ".");
  const amount = selected ? Number(qty) * Number(selected.purchase_price) : 0;
  const candidateItems = candidates.data?.items ?? [];
  const candidateResultsCurrent = searchInput.trim() === search;
  const selectableCandidateItems = candidateResultsCurrent ? candidateItems : [];

  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput.trim()), 250);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => setHighlight(0), [branchId, search]);

  useEffect(() => {
    if (!branchId && availableBranches.length === 1) setBranchId(availableBranches[0]!.id);
  }, [availableBranches, branchId]);

  useEffect(() => {
    onDirtyChange(
      !createdReturn && (form.formState.isDirty || selected !== null || searchInput.trim() !== ""),
    );
  }, [createdReturn, form.formState.isDirty, onDirtyChange, searchInput, selected]);

  const chooseCandidate = (candidate: SupplierReturnCandidate) => {
    setSelected(candidate);
    form.setValue("batch_id", candidate.batch_id, { shouldValidate: true });
    form.setValue("source_document_id", candidate.source_document_id, { shouldValidate: true });
    form.setValue("qty", "", { shouldDirty: true });
    form.clearErrors(["batch_id", "source_document_id", "qty"]);
  };

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      let firstInvalidField: keyof FormValues | null = null;
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        const field = path as keyof FormValues;
        firstInvalidField ??= field;
        form.setError(field, { message: issue.message });
      }
      if (firstInvalidField === "batch_id" || firstInvalidField === "source_document_id") {
        document.getElementById("supplier_return_search")?.focus();
      } else if (firstInvalidField) {
        form.setFocus(firstInvalidField);
      }
      return;
    }
    if (!selected || selected.batch_id !== parsed.data.batch_id) {
      form.setError("batch_id", { message: "Выберите партию заново" });
      return;
    }
    const normalizedQty = parsed.data.qty.replace(",", ".");
    if (Number(normalizedQty) > Number(selected.qty_remaining)) {
      form.setError("qty", {
        message: `Доступно не более ${formatSupplierQuantity(selected.qty_remaining)}`,
      });
      return;
    }
    if (!online) {
      setTopError("Нет подключения к серверу. Возврат не будет записан офлайн.");
      return;
    }

    setTopError(null);
    try {
      const result = await createReturn.mutateAsync({
        operation_id: operationId,
        supplier_id: supplier.id,
        batch_id: selected.batch_id,
        source_document_id: selected.source_document_id,
        qty: normalizedQty,
        reason: parsed.data.reason,
        comment: parsed.data.comment?.trim() || null,
      });
      onDirtyChange(false);
      setCreatedReturn(result);
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось оформить возврат поставщику"));
    }
  });

  if (createdReturn && selected) {
    return (
      <section className="space-y-4" aria-live="polite">
        <div className="rounded-lg border border-success/30 bg-success-subtle px-4 py-4">
          <h2 className="text-lg font-semibold text-success-foreground">Возврат оформлен</h2>
          <p className="mt-1 text-sm text-foreground-secondary">
            Остаток партии уменьшен. Операция сохранена и больше не изменяется.
          </p>
        </div>
        <dl className="grid grid-cols-1 gap-3 rounded-lg border border-border px-4 py-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-foreground-muted">Товар</dt>
            <dd className="mt-1 font-medium text-foreground">{selected.catalog_name}</dd>
          </div>
          <div>
            <dt className="text-foreground-muted">Аптечная точка</dt>
            <dd className="mt-1 font-medium text-foreground">{selected.branch_name}</dd>
          </div>
          <div>
            <dt className="text-foreground-muted">Количество</dt>
            <dd className="mt-1 font-mono font-medium tabular-nums text-foreground">
              {formatSupplierQuantity(createdReturn.qty)} ед.
            </dd>
          </div>
          <div>
            <dt className="text-foreground-muted">Сумма</dt>
            <dd className="mt-1 font-mono font-medium tabular-nums text-foreground">
              {formatSupplierMoney(createdReturn.amount, createdReturn.currency)}
            </dd>
          </div>
        </dl>
        <p className="text-xs text-foreground-muted">
          Код операции: {createdReturn.id.slice(0, 8).toUpperCase()}
        </p>
        <div className="flex justify-end">
          <Button type="button" onClick={onClose}>
            Готово
          </Button>
        </div>
      </section>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="supplier_return_branch">Аптечная точка</Label>
        <Select
          id="supplier_return_branch"
          value={branchId}
          disabled={branches.isLoading || Boolean(branches.error)}
          onChange={(event) => {
            setBranchId(event.target.value);
            setSelected(null);
            form.resetField("batch_id");
            form.resetField("source_document_id");
            form.resetField("qty");
          }}
        >
          <option value="">Выберите точку</option>
          {availableBranches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.name}
            </option>
          ))}
        </Select>
        {!branches.isLoading && !branches.error && availableBranches.length === 0 ? (
          <FormError>Нет доступных точек для возврата поставщику.</FormError>
        ) : null}
        {branches.error ? (
          <div
            className="mt-2 flex flex-wrap items-center gap-2 text-sm text-danger-foreground"
            role="alert"
          >
            <span>{describeApiError(branches.error, "Не удалось загрузить аптечные точки")}</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void branches.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : (
          <p className="mt-1 text-xs text-foreground-muted">
            Остаток будет списан именно с выбранной точки.
          </p>
        )}
      </div>

      <div>
        <Label htmlFor="supplier_return_search">Товар, партия или документ прихода</Label>
        <Input
          id="supplier_return_search"
          value={searchInput}
          disabled={!branchId}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Начните вводить или выберите из списка"
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={Boolean(branchId)}
          aria-controls={candidateListId}
          aria-busy={candidates.isFetching || undefined}
          aria-activedescendant={
            selectableCandidateItems[highlight]
              ? `${candidateListId}-${selectableCandidateItems[highlight]?.batch_id}`
              : undefined
          }
          onKeyDown={(event) => {
            if (candidates.isFetching || selectableCandidateItems.length === 0) {
              return;
            }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setHighlight((current) => Math.min(current + 1, selectableCandidateItems.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setHighlight((current) => Math.max(current - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              const candidate = selectableCandidateItems[highlight];
              if (candidate) chooseCandidate(candidate);
            }
          }}
        />
      </div>

      <div
        id={candidateListId}
        className="max-h-64 overflow-y-auto rounded-lg border border-border"
        role="listbox"
        aria-label="Доступные партии для возврата"
      >
        {!branchId ? (
          <p className="px-4 py-5 text-sm text-foreground-muted">
            Сначала выберите аптечную точку.
          </p>
        ) : !candidateResultsCurrent || candidates.isLoading ? (
          <p className="px-4 py-5 text-sm text-foreground-muted" role="status">
            Загрузка доступных партий…
          </p>
        ) : candidates.error ? (
          <div className="px-4 py-4 text-sm text-danger-foreground" role="alert">
            <p>{describeApiError(candidates.error, "Не удалось загрузить партии")}</p>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => void candidates.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : selectableCandidateItems.length ? (
          selectableCandidateItems.map((candidate, index) => {
            const subtitle = supplierProductSubtitle(candidate);
            const active = selected?.batch_id === candidate.batch_id;
            return (
              <button
                key={candidate.batch_id}
                id={`${candidateListId}-${candidate.batch_id}`}
                type="button"
                role="option"
                aria-selected={active}
                onPointerEnter={() => setHighlight(index)}
                onClick={() => chooseCandidate(candidate)}
                className={cn(
                  "flex min-h-16 w-full items-start justify-between gap-4 border-b border-border px-4 py-3 text-left last:border-b-0",
                  active ? "bg-primary-subtle" : "hover:bg-foreground/[0.03]",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-foreground">
                    {candidate.catalog_name}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-foreground-muted">
                    {subtitle || "Без дополнительных характеристик"} · {candidate.branch_name}
                  </span>
                  <span className="mt-1 block text-xs text-foreground-muted">
                    Партия {candidate.batch_number ?? "без номера"} · приход{" "}
                    {candidate.document_number ?? "без номера"}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  <span className="block font-mono text-sm font-semibold tabular-nums">
                    {formatSupplierQuantity(candidate.qty_remaining)}
                  </span>
                  <span className="block text-xs text-foreground-muted">
                    до {formatSupplierDate(candidate.expires_at)}
                  </span>
                </span>
              </button>
            );
          })
        ) : (
          <div className="px-4 py-5 text-sm text-foreground-muted">
            <p>
              {search
                ? "По вашему запросу партии не найдены."
                : "В выбранной точке нет принятых партий этого поставщика с доступным остатком."}
            </p>
            {search ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-2"
                onClick={() => {
                  setSearchInput("");
                  setSearch("");
                }}
              >
                Очистить поиск
              </Button>
            ) : null}
          </div>
        )}
      </div>
      <FormError>{form.formState.errors.batch_id?.message}</FormError>

      {selected && (
        <div className="space-y-3">
          <div className="rounded-lg border border-border bg-background/60 px-4 py-3 text-sm">
            <p className="font-semibold text-foreground">{selected.catalog_name}</p>
            <p className="mt-1 text-foreground-secondary">
              {selected.branch_name} · партия {selected.batch_number ?? "без номера"} · приход{" "}
              {selected.document_number ?? "без номера"}
            </p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="supplier_return_qty">Количество</Label>
                <button
                  type="button"
                  className="min-h-11 px-2 text-sm font-medium text-primary hover:underline"
                  onClick={() =>
                    form.setValue("qty", selected.qty_remaining, {
                      shouldDirty: true,
                      shouldValidate: true,
                    })
                  }
                >
                  Весь остаток
                </button>
              </div>
              <Input
                id="supplier_return_qty"
                inputMode="decimal"
                autoComplete="off"
                invalid={Boolean(form.formState.errors.qty)}
                {...form.register("qty")}
              />
              <FormError>{form.formState.errors.qty?.message}</FormError>
            </div>
            <div>
              <Label htmlFor="supplier_return_reason">Причина</Label>
              <Select
                id="supplier_return_reason"
                invalid={Boolean(form.formState.errors.reason)}
                {...form.register("reason")}
              >
                <option value="">Выберите причину</option>
                {supplierReturnReasonOptions.map((reason) => (
                  <option key={reason} value={reason}>
                    {supplierReturnReasonLabel[reason]}
                  </option>
                ))}
              </Select>
              <FormError>{form.formState.errors.reason?.message}</FormError>
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="supplier_return_comment">Комментарий</Label>
              <Textarea
                id="supplier_return_comment"
                rows={3}
                placeholder="Например, номер акта или описание дефекта"
                invalid={Boolean(form.formState.errors.comment)}
                {...form.register("comment")}
              />
              <FormError>{form.formState.errors.comment?.message}</FormError>
            </div>
          </div>
        </div>
      )}

      {selected && Number.isFinite(amount) && amount > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-warning-subtle px-4 py-3 text-sm text-warning-foreground">
          <span>Сумма по закупочной цене</span>
          <strong className="font-mono tabular-nums">
            {formatSupplierMoney(amount, selected.currency)}
          </strong>
        </div>
      )}

      {!online && (
        <p className="rounded-lg border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
          Нет подключения к серверу. Возврат станет доступен после восстановления связи.
        </p>
      )}
      {topError && (
        <p role="alert" className="text-sm text-danger">
          {topError}
        </p>
      )}

      <p className="text-xs leading-5 text-foreground-muted">
        Подтверждённый возврат уменьшает остаток партии и сохраняется как неизменяемая операция.
      </p>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" className="min-h-11" onClick={onClose}>
          Отмена
        </Button>
        <Button
          type="submit"
          className="min-h-11"
          disabled={!online || !selected}
          isLoading={form.formState.isSubmitting}
        >
          Подтвердить возврат
        </Button>
      </div>
    </form>
  );
}
