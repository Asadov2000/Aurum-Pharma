import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Checkbox, FormError, Label, Modal, Select, Textarea } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { useTenantAccessPolicy } from "@/features/tenantAccess/queries";
import { describeApiError } from "@/lib/errorMessages";

import {
  mixedDispositionInputSchema,
  mixedDispositionQuantity,
  reviewedMixedDispositionTarget,
  type ResolvedMixedDisposition,
} from "./mixedDisposition";
import { mixedDispositionLabels, mixedDispositionReasons } from "./mixedDispositionLabels";
import {
  acknowledgeMixedDisposition,
  inspectMixedDisposition,
  loadMixedDisposition,
  runMixedDisposition,
  type PendingMixedDisposition,
} from "./mixedDispositionRecovery";
import { type MixedCustomerReturnItem } from "./types";

const formSchema = z.object({
  disposition_type: z.enum(["disposed", "supplier_claim", "regulatory_transfer"]),
  reason_code: z.enum(["damaged", "quality_issue", "wrong_item", "expired", "other"]),
  comment: z.string().refine((value) => Array.from(value).length <= 2000, "Не более 2000 символов"),
  confirmed: z.literal(true, {
    error: "Подтвердите фактическое выполнение",
  }),
});
interface FormValues {
  disposition_type: "" | keyof typeof mixedDispositionLabels;
  reason_code: "" | keyof typeof mixedDispositionReasons;
  comment: string;
  confirmed: boolean;
}
function errorMessage(error: unknown): string {
  return error instanceof Error && /[а-яё]/i.test(error.message)
    ? error.message
    : describeApiError(error, "Не удалось проверить решение. Сохранённый запрос остаётся прежним.");
}

export function MixedDispositionModal({
  scope,
  item,
  pending: initialPending,
  onClose,
  onResolved,
}: {
  scope: PosStorageScope;
  item: MixedCustomerReturnItem | null;
  pending: PendingMixedDisposition | null;
  onClose: () => void;
  onResolved: () => void;
}): JSX.Element {
  const { user } = useAuth();
  const [operationScope] = useState(() => ({
    ...scope,
    branchId: initialPending?.branchId ?? item?.branch_id ?? "",
  }));
  const [guard] = useState(() => capturePosSessionGuard(scope));
  const mounted = useRef(true);
  const active = useCallback(() => mounted.current && guard(), [guard]);
  const [pending, setPending] = useState(initialPending);
  const [result, setResult] = useState<ResolvedMixedDisposition | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const policy = useTenantAccessPolicy(scope.userId, scope.tenantId, true);
  const canWrite =
    active() &&
    !policy.error &&
    !user?.support_access &&
    hasPermissionForBranch(user, "customer_returns.resolve", operationScope.branchId) &&
    policy.data?.tenant_id === scope.tenantId &&
    policy.data.mode === "full" &&
    !policy.data.blocked_modules.includes("pos");
  const form = useForm<FormValues>({
    defaultValues: { disposition_type: "", reason_code: "", comment: "", confirmed: false },
  });
  const inspect = useCallback(async (): Promise<void> => {
    if (!active()) return;
    setBusy(true);
    setError(null);
    setNotFound(false);
    try {
      const saved = loadMixedDisposition(scope);
      if (!saved) throw new Error("Сохранённое решение отсутствует. Откройте очередь заново.");
      const status = await inspectMixedDisposition(operationScope);
      if (!active()) return;
      setPending(saved);
      if (status?.state === "resolved") setResult(status);
      else if (status?.state === "not_found") setNotFound(true);
      else throw new Error("Не удалось подтвердить состояние сохранённого решения.");
    } catch (reason) {
      if (active()) setError(errorMessage(reason));
    } finally {
      if (active()) setBusy(false);
    }
  }, [active, operationScope, scope]);
  useEffect(() => {
    mounted.current = true;
    if (initialPending) void inspect();
    return () => {
      mounted.current = false;
    };
  }, [initialPending, inspect]);

  const submit = form.handleSubmit(async (values) => {
    form.clearErrors();
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string")
          form.setError(field as keyof FormValues, { message: issue.message });
      }
      return;
    }
    if (!item || !canWrite || busy || pending || !active()) return;
    setBusy(true);
    setError(null);
    try {
      const target =
        item.inventory_kind === "legacy_pack"
          ? {
              inventory_kind: item.inventory_kind,
              intake_operation_id: item.operation_id,
              line_id: item.line_id,
              batch_id: item.batch_id,
            }
          : {
              inventory_kind: item.inventory_kind,
              intake_operation_id: item.operation_id,
              line_id: item.line_id,
              lot_id: item.lot_id,
            };
      const input = mixedDispositionInputSchema.parse({
        target,
        disposition_type: parsed.data.disposition_type,
        reason_code: parsed.data.reason_code,
        comment: parsed.data.comment || null,
      });
      const saved = await runMixedDisposition(
        operationScope,
        input,
        reviewedMixedDispositionTarget(item),
      );
      if (active()) {
        setResult(saved);
        onResolved();
      }
    } catch (reason) {
      if (active()) setError(errorMessage(reason));
    } finally {
      if (active()) {
        try {
          setPending(loadMixedDisposition(scope));
        } catch (reason) {
          setError(errorMessage(reason));
        }
        setBusy(false);
      }
    }
  });
  const retry = async (): Promise<void> => {
    if (!notFound || !pending || !canWrite || busy || !active()) return;
    setBusy(true);
    setError(null);
    setNotFound(false);
    try {
      const saved = await runMixedDisposition(operationScope);
      if (active()) {
        setResult(saved);
        onResolved();
      }
    } catch (reason) {
      if (active()) setError(errorMessage(reason));
    } finally {
      if (active()) setBusy(false);
    }
  };
  const acknowledge = async (): Promise<void> => {
    if (!result || busy || !active()) return;
    setBusy(true);
    setError(null);
    try {
      const cleared = await acknowledgeMixedDisposition(operationScope, result);
      if (!active()) return;
      if (!cleared) throw new Error("Результат не подтверждён. Сохранённый запрос не удалён.");
      onResolved();
      onClose();
    } catch (reason) {
      if (active()) setError(errorMessage(reason));
    } finally {
      if (active()) setBusy(false);
    }
  };
  const reviewed = pending?.reviewedTarget ?? (item ? reviewedMixedDispositionTarget(item) : null);
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Решение по смешанному карантину"
      className="max-w-2xl"
    >
      {error ? (
        <p role="alert" className="mb-4 text-sm text-danger">
          {error}
        </p>
      ) : null}
      {!guard() ? <p role="alert">Аккаунт изменился. Откройте раздел заново.</p> : null}
      {policy.error ? (
        <div role="alert" className="mb-4 space-y-2">
          <p>Не удалось проверить возможность записи. Проверка сохранённого результата доступна.</p>
          <Button
            variant="secondary"
            disabled={policy.isFetching}
            onClick={() => void policy.refetch()}
          >
            Повторить проверку доступа
          </Button>
        </div>
      ) : null}
      {result ? (
        <div className="space-y-4">
          <p role="status" className="font-semibold">
            Решение сохранено
          </p>
          <p>
            {mixedDispositionLabels[result.disposition_type]} ·{" "}
            {mixedDispositionQuantity(result.target)}
          </p>
          <p>{mixedDispositionReasons[result.reason_code]}</p>
          <Button isLoading={busy} disabled={busy || !guard()} onClick={() => void acknowledge()}>
            Завершить проверку
          </Button>
        </div>
      ) : pending ? (
        <div className="space-y-4">
          <p>Сохранённое действие: {mixedDispositionLabels[pending.request.disposition_type]}.</p>
          {reviewed ? <p>Весь принятый объём: {mixedDispositionQuantity(reviewed)}</p> : null}
          <p>Причина: {mixedDispositionReasons[pending.request.reason_code]}.</p>
          {pending.request.comment ? (
            <p className="break-words">{pending.request.comment}</p>
          ) : null}
          <p>
            Сначала проверьте результат. Повтор сохранит исходное решение и не создаст новую
            операцию. При конфликте или повреждении данных обратитесь к администратору.
          </p>
          <Button
            variant="secondary"
            disabled={busy || !guard()}
            isLoading={busy}
            onClick={() => void inspect()}
          >
            Проверить результат
          </Button>
          {notFound ? (
            <>
              <p role="status">Решение с этим номером пока не найдено.</p>
              <Button disabled={busy || !canWrite} onClick={() => void retry()}>
                Повторить сохранённый запрос
              </Button>
            </>
          ) : null}
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <p className="font-semibold">{item?.catalog_name}</p>
          {reviewed ? <p>Весь принятый объём: {mixedDispositionQuantity(reviewed)}</p> : null}
          <p>
            Подтверждайте только уже выполненное действие. После сохранения решение нельзя изменить;
            товар не вернётся в продажу.
          </p>
          <fieldset disabled={busy || !canWrite}>
            <legend className="font-semibold">Фактически выполненное действие</legend>
            {Object.entries(mixedDispositionLabels).map(([value, label]) => (
              <label key={value} className="mt-2 flex items-start gap-2">
                <input type="radio" value={value} {...form.register("disposition_type")} />
                {label}
              </label>
            ))}
            <FormError>{form.formState.errors.disposition_type?.message}</FormError>
            <Label htmlFor="mixed_disposition_reason">Причина решения</Label>
            <Select id="mixed_disposition_reason" {...form.register("reason_code")}>
              <option value="">Выберите причину</option>
              {Object.entries(mixedDispositionReasons).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.reason_code?.message}</FormError>
            <Label htmlFor="mixed_disposition_comment">Комментарий к решению</Label>
            <Textarea
              id="mixed_disposition_comment"
              rows={3}
              maxLength={4000}
              {...form.register("comment")}
            />
            <FormError>{form.formState.errors.comment?.message}</FormError>
            <label className="mt-4 flex items-start gap-2">
              <Checkbox {...form.register("confirmed")} />
              Подтверждаю, что действие фактически выполнено для всего указанного количества
            </label>
            <FormError>{form.formState.errors.confirmed?.message}</FormError>
          </fieldset>
          <Button type="submit" disabled={busy || !canWrite} isLoading={busy}>
            Сохранить решение
          </Button>
        </form>
      )}
      {!canWrite && !result ? (
        <p className="mt-4 text-sm text-foreground-muted">
          Запись доступна только при действующем доступе к точке и разрешённых изменениях в аптеке.
        </p>
      ) : null}
      <Button className="mt-4" variant="ghost" disabled={busy} onClick={onClose}>
        Закрыть
      </Button>
    </Modal>
  );
}
