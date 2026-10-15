import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { isAxiosError } from "axios";
import { Button, ConfirmDialog, Input, Label, Select } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { describeApiError } from "@/features/foundation/errors";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { useTenantAccessPolicy } from "@/features/tenantAccess/queries";
import { useConnectivity } from "@/lib/connectivityContext";
import {
  legacyPackagingSchema,
  previewLegacyReconciliation,
  saveLegacyReconciliation,
  readLegacyReconciliation,
  postLegacyTransition,
  readLegacyTransition,
  type LegacyCount,
  type LegacyPreview,
  type LegacyRecord,
  type LegacyResult,
  type LegacyScope,
} from "./legacyTransition";
import type { BatchDetails } from "./types";
import { physicalUnitLabels } from "./physicalLots";

const fieldsSchema = z.object({
  unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
  units: z.string().regex(/^[1-9]\d*$/),
  blister: z.string().regex(/^$|^[1-9]\d*$/),
  step: z.string().regex(/^[1-9]\d*$/),
  closed: z.string().regex(/^[1-9]\d*$/),
  noOpened: z.literal(true),
});
interface Fields {
  unit: string;
  units: string;
  blister: string;
  step: string;
  closed: string;
  noOpened: boolean;
}
type Checked = { count: LegacyCount; preview: LegacyPreview; fingerprint: string };

export function LegacyTransitionPanel({ batch }: { batch: BatchDetails }): JSX.Element | null {
  const { user } = useAuth();
  if (
    !user?.active_tenant_id ||
    user.active_tenant_id !== batch.tenant_id ||
    user.support_access ||
    !["batches.view", "batches.view_costs", "batches.update", "incoming.view"].every((permission) =>
      hasPermissionForBranch(user, permission, batch.branch_id),
    )
  )
    return null;
  return (
    <TransitionWorkspace
      key={JSON.stringify([
        user.id,
        user.active_tenant_id,
        user.permissions,
        user.permission_scopes,
        batch.branch_id,
        batch.id,
      ])}
      batch={batch}
      context={{
        tenant_id: user.active_tenant_id,
        actor_id: user.id,
        branch_id: batch.branch_id,
        batch_id: batch.id,
        catalog_id: batch.catalog_id,
      }}
    />
  );
}

function TransitionWorkspace({
  batch,
  context,
}: {
  batch: BatchDetails;
  context: LegacyScope;
}): JSX.Element {
  const client = useQueryClient();
  const online = useConnectivity().canUseServer;
  const policy = useTenantAccessPolicy(context.actor_id, context.tenant_id, true);
  const writable =
    online &&
    !policy.error &&
    policy.data?.tenant_id === context.tenant_id &&
    policy.data.mode === "full" &&
    !policy.data.blocked_modules.includes("inventory");
  const eligible = !batch.is_blocked && batch.days_to_expiry > 0 && Number(batch.qty_remaining) > 0;
  const [guard] = useState(() =>
    capturePosSessionGuard({ tenantId: context.tenant_id, userId: context.actor_id }),
  );
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const active = () => mounted.current && guard();
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [checked, setChecked] = useState<Checked | null>(null);
  const [record, setRecord] = useState<LegacyRecord | null>(null);
  const [saveId, setSaveId] = useState<string | null>(null);
  const [commandId, setCommandId] = useState<string | null>(null);
  const [result, setResult] = useState<LegacyResult | null>(null);
  const [confirm, setConfirm] = useState(false);
  const form = useForm<Fields>({
    defaultValues: { unit: "", units: "", blister: "", step: "", closed: "", noOpened: false },
  });
  const recovery = useForm<{ recordId: string; commandId: string }>({
    defaultValues: { recordId: "", commandId: "" },
  });
  const fingerprint = JSON.stringify(form.watch());
  const current = checked?.fingerprint === fingerprint ? checked : null;
  const refresh = () =>
    Promise.all(
      ["inventory", "reports", "pos"].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
  const run = async (action: () => Promise<void>): Promise<void> => {
    if (inFlight.current || !active() || !online) return;
    inFlight.current = true;
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (error) {
      if (active())
        setMessage(
          describeApiError(
            error,
            "Результат не подтверждён. Проверьте сохранение по коду; не создавайте повторный перенос.",
          ),
        );
    } finally {
      inFlight.current = false;
      if (active()) setBusy(false);
    }
  };
  const preview = form.handleSubmit(async (values) => {
    if (!writable || !eligible || saveId || commandId) return;
    const parsed = fieldsSchema.safeParse(values);
    if (!parsed.success) {
      setMessage("Заполните целые количества и подтвердите отсутствие открытых упаковок.");
      return;
    }
    const packaging = legacyPackagingSchema.safeParse({
      version: 2,
      unit: parsed.data.unit,
      units_per_pack: Number(values.units),
      units_per_blister: values.blister ? Number(values.blister) : null,
      dispense_step: Number(values.step),
    });
    const closed = Number(values.closed);
    if (
      !packaging.success ||
      !Number.isSafeInteger(closed) ||
      closed * (packaging.success ? packaging.data.units_per_pack : 0) > 99999999999
    ) {
      setMessage("Проверьте состав упаковки, шаг отпуска и количество.");
      return;
    }
    const count: LegacyCount = {
      packaging: packaging.data,
      closed_packs: closed,
      opened_packs: [],
    };
    const submitted = JSON.stringify(values);
    setChecked(null);
    await run(async () => {
      const response = await previewLegacyReconciliation(context, count);
      if (active()) setChecked({ count, preview: response, fingerprint: submitted });
    });
  });
  const save = () =>
    run(async () => {
      if (
        !writable ||
        !eligible ||
        !current ||
        saveId ||
        commandId ||
        current.preview.comparison !== "matched" ||
        !current.preview.recorded_quantity_representable
      )
        return;
      const id = crypto.randomUUID();
      setSaveId(id);
      recovery.setValue("recordId", id);
      try {
        const response = await saveLegacyReconciliation(
          context,
          current.count,
          id,
          current.preview.source.legacy_quantity,
        );
        if (active()) setRecord(response);
      } catch (error) {
        if (active() && isAxiosError(error) && [400, 422].includes(error.response?.status ?? 0)) {
          setSaveId(null);
          setChecked(null);
          recovery.setValue("recordId", "");
        }
        throw error;
      }
    });
  const recoverRecord = () =>
    run(async () => {
      if (record || commandId || result) return;
      const id = z
        .string()
        .uuid()
        .parse(saveId ?? recovery.getValues("recordId"));
      setSaveId(id);
      const response = await readLegacyReconciliation(
        context,
        id,
        checked
          ? {
              count: checked.count,
              quantity: checked.preview.source.legacy_quantity,
            }
          : undefined,
      );
      if (active()) setRecord(response);
    });
  const recoverTransfer = () =>
    run(async () => {
      if (result) return;
      const id = z
        .string()
        .uuid()
        .parse(commandId ?? recovery.getValues("commandId"));
      setCommandId(id);
      const response = await readLegacyTransition(context, id);
      if (active()) {
        setResult(response);
        await refresh();
      }
    });
  const transfer = () =>
    run(async () => {
      setConfirm(false);
      if (
        !writable ||
        !eligible ||
        !record ||
        commandId ||
        result ||
        record.preview.comparison !== "matched" ||
        !record.preview.recorded_quantity_representable
      )
        return;
      const id = crypto.randomUUID();
      setCommandId(id);
      recovery.setValue("commandId", id);
      try {
        const response = await postLegacyTransition(context, record.id, id);
        if (active()) setResult(response);
      } finally {
        if (active()) await refresh();
      }
    });
  return (
    <section
      aria-label="Перенос в поштучный учёт"
      className="space-y-3 rounded-lg border border-border p-4"
    >
      <h3 className="font-semibold">Перенос в поштучный учёт</h3>
      <p className="text-sm">
        Только целые закрытые упаковки. Пересчитайте товар и укажите фактический состав старой
        упаковки. Открытые упаковки пока перенести нельзя.
      </p>
      {!writable && (
        <p role="status" className="text-sm">
          Изменения недоступны. Проверка сохранённых результатов доступна при соединении с сервером.
        </p>
      )}
      {!record && !saveId && !commandId && (
        <form onSubmit={(event) => void preview(event)} className="space-y-3">
          <fieldset disabled={busy || !writable || !eligible} className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="legacy-unit">Единица</Label>
              <Select id="legacy-unit" {...form.register("unit")}>
                <option value="">Выберите</option>
                <option value="tablet">Таблетка</option>
                <option value="capsule">Капсула</option>
                <option value="ampoule">Ампула</option>
                <option value="sachet">Пакетик</option>
                <option value="suppository">Суппозиторий</option>
              </Select>
            </div>
            {(
              [
                ["units", "Единиц в упаковке"],
                ["blister", "Единиц в блистере (необязательно)"],
                ["step", "Шаг отпуска"],
                ["closed", "Закрытых упаковок"],
              ] as const
            ).map(([name, label]) => (
              <div key={name}>
                <Label htmlFor={`legacy-${name}`}>{label}</Label>
                <Input id={`legacy-${name}`} inputMode="numeric" {...form.register(name)} />
              </div>
            ))}
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" {...form.register("noOpened")} />
              Подтверждаю: открытых упаковок нет
            </label>
            <Button type="submit">Проверить пересчёт</Button>
          </fieldset>
          {current && (
            <div className="space-y-2 text-sm">
              <p>
                По учёту: {current.preview.recorded_base_units} ед. Фактически:{" "}
                {current.preview.observed_base_units} ед.
              </p>
              <p>
                {current.preview.comparison === "matched" &&
                current.preview.recorded_quantity_representable
                  ? "Количество совпадает. Это ещё не перенос остатков."
                  : "Есть расхождение. Перенос недоступен; проверьте пересчёт."}
              </p>
              <Button
                disabled={
                  busy ||
                  !writable ||
                  !eligible ||
                  current.preview.comparison !== "matched" ||
                  !current.preview.recorded_quantity_representable
                }
                onClick={() => void save()}
              >
                Сохранить пересчёт
              </Button>
            </div>
          )}
        </form>
      )}
      {saveId && <p className="break-all text-sm">Код пересчёта: {saveId}</p>}
      {commandId && <p className="break-all text-sm">Код переноса: {commandId}</p>}
      {(saveId || commandId) && (
        <p className="text-sm">
          Сохраните коды до закрытия карточки: они нужны для проверки после потери соединения. При
          неопределённом результате сначала проверьте его по коду.
        </p>
      )}
      {record && !result && (
        <div className="space-y-2 text-sm">
          <p>
            Пересчёт сохранён: {record.preview.observation.closed_packs} закрытых упаковок,{" "}
            {record.preview.observed_base_units} ед.
          </p>
          <p>
            Единица: {physicalUnitLabels[record.preview.observation.packaging.unit]}. В упаковке:{" "}
            {record.preview.observation.packaging.units_per_pack}; в блистере:{" "}
            {record.preview.observation.packaging.units_per_blister ?? "не указано"}; шаг отпуска:{" "}
            {record.preview.observation.packaging.dispense_step}.
          </p>
          <Button
            disabled={
              busy ||
              !writable ||
              !eligible ||
              Boolean(commandId) ||
              record.preview.comparison !== "matched" ||
              !record.preview.recorded_quantity_representable
            }
            onClick={() => setConfirm(true)}
          >
            Перенести остаток
          </Button>
        </div>
      )}
      {!result && (
        <div className="grid gap-3 sm:grid-cols-2">
          {!record && !commandId && (
            <div>
              <Label htmlFor="legacy-recover-record">Код пересчёта</Label>
              <Input
                id="legacy-recover-record"
                disabled={busy || Boolean(saveId)}
                {...recovery.register("recordId")}
              />
              <Button
                disabled={busy || !online}
                variant="secondary"
                onClick={() => void recoverRecord()}
              >
                Проверить сохранение
              </Button>
            </div>
          )}
          <div>
            <Label htmlFor="legacy-recover-command">Код переноса</Label>
            <Input
              id="legacy-recover-command"
              disabled={busy || Boolean(commandId)}
              {...recovery.register("commandId")}
            />
            <Button
              disabled={busy || !online}
              variant="secondary"
              onClick={() => void recoverTransfer()}
            >
              Проверить перенос
            </Button>
          </div>
        </div>
      )}
      {result && (
        <p role="status" className="break-all text-sm">
          Перенос подтверждён. Физическая партия: {result.lot_id}
        </p>
      )}
      {message && (
        <p role="alert" className="text-sm text-danger">
          {message}
        </p>
      )}
      <ConfirmDialog
        open={confirm && writable && eligible}
        title="Перенести остаток в поштучный учёт?"
        message="Остаток старой партии будет списан и перенесён в физическую партию. Отменить этот перенос в карточке нельзя. Сервер повторно проверит остаток и документы."
        confirmLabel="Подтвердить перенос"
        isLoading={busy}
        onConfirm={() => void transfer()}
        onCancel={() => setConfirm(false)}
      />
    </section>
  );
}
