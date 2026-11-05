import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Input, Label, Modal, Pagination, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useAuth } from "@/features/auth/hooks";
import { type PhysicalLot } from "./physicalLots";
import { writeOffReasonLabel, writeOffReasonOptions } from "./labels";
import { getPhysicalWriteoffStock, type PhysicalWriteoffInput } from "./physicalWriteoff";

const quantity = z
  .string()
  .regex(/^(0|[1-9]\d*)$/)
  .transform(Number)
  .pipe(z.number().int().min(0).max(99999999999));
const fieldsSchema = z.object({
  closed: quantity,
  reason: z.enum(["expired", "damaged", "spoiled", "theft", "other"]),
  comment: z
    .string()
    .max(2000)
    .refine((value) => !value.includes("\0")),
  quantities: z.record(z.string(), quantity),
});
type Fields = z.input<typeof fieldsSchema>;
type Available = { packId: string; number: number | null; units: number };
type Reviewed = { input: PhysicalWriteoffInput; total: string };

export function PhysicalWriteOffModal({
  lot,
  writable,
  onClose,
  onSubmit,
}: {
  lot: PhysicalLot;
  writable: boolean;
  onClose: () => void;
  onSubmit: (input: PhysicalWriteoffInput, total: string) => Promise<void>;
}): JSX.Element {
  const { user } = useAuth();
  const [page, setPage] = useState(1);
  const [reviewed, setReviewed] = useState<Reviewed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectionChanged, setSelectionChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  // Only observed physical components can be selected, including across pages.
  const available = useRef(new Map<string, Available>());
  const observedPages = useRef(new Map<number, string[]>());
  const form = useForm<Fields>({
    defaultValues: {
      closed: "0",
      reason: "damaged",
      comment: "",
      quantities: {},
    },
  });
  const stock = useQuery({
    queryKey: [
      "inventory",
      "write-off-stock",
      user?.id,
      user?.active_tenant_id,
      user?.permission_scopes,
      user?.permissions,
      user?.support_access?.id,
      lot.branch_id,
      lot.lot_id,
      page,
    ],
    queryFn: ({ signal }) => getPhysicalWriteoffStock(lot.lot_id, lot.branch_id, page, signal),
    staleTime: 0,
  });
  const canSubmit =
    writable &&
    !busy &&
    !stock.isFetching &&
    !!stock.data &&
    !stock.error &&
    !stock.data.lot.is_blocked;
  useEffect(() => {
    if (!stock.data) return;
    const previousKeys = observedPages.current.get(page) ?? [];
    for (const key of previousKeys) available.current.delete(key);
    const keys: string[] = [];
    const remember = (packId: string, number: number | null, units: number) => {
      const key = `${packId}_${number ?? "loose"}`;
      keys.push(key);
      available.current.set(key, { packId, number, units });
    };
    for (const pack of stock.data.opened_packs) {
      for (const number of pack.state.intact_blisters)
        remember(pack.pack_id, number, stock.data.lot.packaging.units_per_blister ?? 0);
      for (const blister of pack.state.opened_blisters)
        remember(pack.pack_id, blister.number, blister.remaining_units);
      if (pack.state.loose_units > 0) remember(pack.pack_id, null, pack.state.loose_units);
    }
    for (const key of previousKeys) {
      if (keys.includes(key)) continue;
      const value = form.getValues(`quantities.${key}`);
      if (value && value !== "0") {
        form.setValue(`quantities.${key}`, "0");
        setSelectionChanged(true);
      }
    }
    observedPages.current.set(page, keys);
    setReviewed(null);
  }, [stock.data, page, form]);
  function review(values: Fields): void {
    setError(null);
    const parsed = fieldsSchema.safeParse(values);
    if (!parsed.success) {
      setError("Введите целые неотрицательные количества и комментарий до 2000 символов.");
      return;
    }
    if (!canSubmit || !stock.data) return;
    if (BigInt(parsed.data.closed) > BigInt(stock.data.lot.closed_packs)) {
      setError("Закрытых упаковок недостаточно. Обновите остатки.");
      return;
    }
    const packs = new Map<string, PhysicalWriteoffInput["selection"]["opened_packs"][number]>();
    let total = BigInt(parsed.data.closed) * BigInt(stock.data.lot.packaging.units_per_pack);
    for (const [key, units] of Object.entries(parsed.data.quantities)) {
      if (units === 0) continue;
      const component = available.current.get(key);
      if (!component || units > component.units) {
        setError("Выбрано больше единиц, чем доступно в конкретном блистере или россыпи.");
        return;
      }
      const selection = packs.get(component.packId) ?? {
        pack_id: component.packId,
        blisters: [],
        loose_units: 0,
      };
      if (component.number === null) selection.loose_units = units;
      else selection.blisters.push({ number: component.number, units });
      packs.set(component.packId, selection);
      total += BigInt(units);
    }
    if (total === 0n || total > 99999999999n) {
      setError("Выберите количество для списания от 1 до 99999999999 единиц.");
      return;
    }
    setReviewed({
      total: total.toString(),
      input: {
        branch_id: lot.branch_id,
        lot_id: lot.lot_id,
        reason: parsed.data.reason,
        comment: parsed.data.comment || null,
        selection: { closed_packs: parsed.data.closed, opened_packs: [...packs.values()] },
      },
    });
  }
  const componentInput = (packId: string, number: number | null, units: number) => {
    const key = `${packId}_${number ?? "loose"}`;
    const label = number === null ? `Россыпь · ${packId}` : `Блистер ${number} · ${packId}`;
    return (
      <div key={key}>
        <Label htmlFor={key}>{label}</Label>
        <p className="text-sm">Доступно: {units}</p>
        <Input
          id={key}
          inputMode="numeric"
          defaultValue="0"
          {...form.register(`quantities.${key}`)}
        />
      </div>
    );
  };
  return (
    <Modal
      open
      title="Списание поштучного остатка"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="space-y-4">
        <p>
          {lot.catalog_name ?? "Без названия"} · {lot.lot_id}
        </p>
        {error && <p role="alert">{error}</p>}
        {selectionChanged && (
          <p role="status">
            Остатки изменились. Недоступные блистеры или россыпь убраны из выбора. Проверьте
            обновлённое списание.
          </p>
        )}
        {stock.error && (
          <p role="alert">
            {describeApiError(stock.error, "Не удалось загрузить состав остатка.")}
          </p>
        )}
        {stock.data?.lot.is_blocked && (
          <p role="alert">Партия заблокирована. Списание запрещено.</p>
        )}
        {reviewed ? (
          <>
            <p>К списанию: {reviewed.total} базовых единиц.</p>
            <p>Закрытых упаковок: {reviewed.input.selection.closed_packs}.</p>
            {reviewed.input.selection.opened_packs.map((pack) => (
              <div key={pack.pack_id}>
                <p>Упаковка {pack.pack_id}</p>
                {pack.blisters.map((blister) => (
                  <p key={blister.number}>
                    Блистер {blister.number}: {blister.units}
                  </p>
                ))}
                {pack.loose_units > 0 && <p>Россыпь: {pack.loose_units}</p>}
              </div>
            ))}
            <p>Причина: {writeOffReasonLabel[reviewed.input.reason]}</p>
            {reviewed.input.comment && <p>Комментарий: {reviewed.input.comment}</p>}
            <p>Списание уменьшит физический остаток. Отменить проведённую операцию нельзя.</p>
            <Button variant="secondary" disabled={busy} onClick={() => setReviewed(null)}>
              Изменить выбор
            </Button>
            <Button
              disabled={!canSubmit}
              isLoading={busy}
              onClick={() => {
                if (!canSubmit || sending.current) return;
                sending.current = true;
                setBusy(true);
                void onSubmit(reviewed.input, reviewed.total)
                  .catch((cause: unknown) => {
                    setError(describeApiError(cause, "Проверьте результат списания."));
                  })
                  .finally(() => {
                    sending.current = false;
                    setBusy(false);
                  });
              }}
            >
              Подтвердить списание
            </Button>
          </>
        ) : (
          <form className="space-y-4" onSubmit={form.handleSubmit(review)}>
            <fieldset disabled={!writable || busy} className="space-y-4">
              <div>
                <Label htmlFor="physical-writeoff-closed">Закрытые упаковки</Label>
                <p>Доступно: {stock.data?.lot.closed_packs ?? "—"}</p>
                <Input
                  id="physical-writeoff-closed"
                  inputMode="numeric"
                  {...form.register("closed")}
                />
              </div>
              <p>
                Во вскрытых упаковках выберите конкретные блистеры или россыпь. Закрытые упаковки
                автоматически не вскрываются.
              </p>
              {stock.data?.opened_packs.map((pack) => (
                <fieldset key={pack.pack_id} className="space-y-3 rounded-lg border p-3">
                  <legend>Упаковка {pack.pack_id}</legend>
                  {pack.state.intact_blisters.map((number) =>
                    componentInput(
                      pack.pack_id,
                      number,
                      stock.data.lot.packaging.units_per_blister ?? 0,
                    ),
                  )}
                  {pack.state.opened_blisters.map((blister) =>
                    componentInput(pack.pack_id, blister.number, blister.remaining_units),
                  )}
                  {pack.state.loose_units > 0 &&
                    componentInput(pack.pack_id, null, pack.state.loose_units)}
                </fieldset>
              ))}
              <Pagination
                page={page}
                pageSize={50}
                total={stock.data?.total ?? 0}
                onPage={setPage}
              />
              <div>
                <Label htmlFor="physical-writeoff-reason">Причина</Label>
                <Select id="physical-writeoff-reason" {...form.register("reason")}>
                  {writeOffReasonOptions.map((reason) => (
                    <option key={reason} value={reason}>
                      {writeOffReasonLabel[reason]}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="physical-writeoff-comment">Комментарий</Label>
                <Textarea
                  id="physical-writeoff-comment"
                  maxLength={2000}
                  {...form.register("comment")}
                />
              </div>
            </fieldset>
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => void stock.refetch()}
            >
              Обновить остатки
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              Проверить списание
            </Button>
          </form>
        )}
      </div>
    </Modal>
  );
}
