import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { isAxiosError } from "axios";
import { useQueryClient } from "@tanstack/react-query";

import { Button, ConfirmDialog } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

import { getIncoming, listIncomingDraftRows } from "./api";
import { incomingKeys, useAcceptIncoming } from "./queries";
import { type IncomingAcceptPayload, type IncomingDocumentWithItems } from "./types";

const intentSchema = z
  .object({
    stock_mode: z.enum(["legacy_pack", "physical_unit_v2"]),
    expected_revision: z
      .string()
      .regex(/^[a-f0-9]{64}$/)
      .optional(),
  })
  .superRefine((value, ctx) => {
    if (value.stock_mode === "physical_unit_v2" && !value.expected_revision) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Обновите документ перед приёмкой." });
    }
  });

class IncomingAcceptanceError extends Error {}

interface Props {
  document: IncomingDocumentWithItems;
  userId: string;
  open: boolean;
  onClose: () => void;
  onBlockedChange: (blocked: boolean) => void;
  onAccepted: () => void;
}

/** Stores only the mode and revision, never medicine details or purchase prices. */
export function IncomingAcceptDialog({
  document,
  userId,
  open,
  onClose,
  onBlockedChange,
  onAccepted,
}: Props): JSX.Element {
  const key = `aurum:incoming-accept:v1:${encodeURIComponent(JSON.stringify([document.tenant_id, userId, document.id]))}`;
  const [generation] = useState(getAuthSessionGeneration);
  const mounted = useRef(true);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restored] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return { intent: raw ? intentSchema.parse(JSON.parse(raw)) : null, invalid: false };
    } catch {
      return { intent: null, invalid: true };
    }
  });
  const [intent, setIntent] = useState<IncomingAcceptPayload | null>(restored.intent);
  const [storageInvalid, setStorageInvalid] = useState(restored.invalid);
  const accept = useAcceptIncoming();
  const queryClient = useQueryClient();
  const form = useForm<{ stock_mode: IncomingAcceptPayload["stock_mode"] }>({
    defaultValues: { stock_mode: intent?.stock_mode ?? "legacy_pack" },
  });
  const mode = form.watch("stock_mode");
  const active = () => {
    const user = useAuthStore.getState().user;
    return (
      mounted.current &&
      generation === getAuthSessionGeneration() &&
      user?.id === userId &&
      user.active_tenant_id === document.tenant_id
    );
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    onBlockedChange(busy || intent !== null || storageInvalid);
  }, [busy, intent, onBlockedChange, storageInvalid]);

  const clearConfirmed = () => {
    localStorage.removeItem(key);
    setIntent(null);
    onAccepted();
    onClose();
  };
  const reconcile = async (pending: IncomingAcceptPayload) => {
    const current = await getIncoming(document.id);
    if (!active()) return false;
    queryClient.setQueryData(incomingKeys.doc(document.id), current);
    if (current.status === "accepted") {
      const matching =
        pending.stock_mode === "physical_unit_v2"
          ? current.stock_mode === "physical_unit_v2"
          : current.stock_mode === "legacy_pack";
      if (!matching)
        throw new IncomingAcceptanceError(
          "Документ принят другим способом. Обратитесь к администратору.",
        );
      clearConfirmed();
      return true;
    }
    if (current.status !== "draft")
      throw new IncomingAcceptanceError("Статус документа изменился. Проверьте документ.");
    return false;
  };
  const submit = form.handleSubmit(async (values) => {
    if (busyRef.current || storageInvalid || !active()) return;
    const parsed = intentSchema.safeParse(
      intent ?? {
        stock_mode: values.stock_mode,
        ...(values.stock_mode === "physical_unit_v2"
          ? { expected_revision: document.acceptance_revision }
          : {}),
      },
    );
    if (
      !parsed.success ||
      (!intent && values.stock_mode === "physical_unit_v2" && !document.physical_receiving_ready)
    ) {
      setError(
        "Для всех позиций нужен сохранённый состав упаковки и целое количество упаковок. Обновите документ после исправления.",
      );
      return;
    }
    const pending = parsed.data;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      if (intent && (await reconcile(pending))) return;
      if (!active()) return;
      const rows = await listIncomingDraftRows(document.id);
      if (!active()) return;
      if (rows.length > 0)
        throw new IncomingAcceptanceError("Сначала завершите или удалите незавершённые позиции.");
      // Persist before any write: a lost response must not allow switching modes.
      if (!intent) {
        localStorage.setItem(key, JSON.stringify(pending));
        setIntent(pending);
      }
      const result = await accept.mutateAsync(
        pending.stock_mode === "legacy_pack" ? document.id : { id: document.id, payload: pending },
      );
      if (!active()) return;
      if (result?.status !== "accepted") {
        if (!(await reconcile(pending)))
          throw new IncomingAcceptanceError("Результат приёмки пока не подтверждён.");
      } else if (result.stock_mode !== pending.stock_mode) {
        throw new IncomingAcceptanceError(
          "Способ приёмки не подтверждён сервером. Проверьте статус документа.",
        );
      } else clearConfirmed();
    } catch (failure) {
      const status = isAxiosError(failure) ? failure.response?.status : undefined;
      if (active() && status && status >= 400 && status < 500 && status !== 408 && status !== 429) {
        try {
          if (!(await reconcile(pending)) && active()) {
            localStorage.removeItem(key);
            setIntent(null);
          }
        } catch {
          // A failed status lookup retains the intent and all write restrictions.
        }
      }
      if (active())
        setError(
          failure instanceof IncomingAcceptanceError
            ? failure.message
            : describeApiError(
                failure,
                "Не удалось подтвердить приёмку. Проверьте статус перед повтором.",
              ),
        );
    } finally {
      busyRef.current = false;
      if (active()) setBusy(false);
    }
  });
  const check = async () => {
    if ((!intent && !storageInvalid) || busyRef.current || !active()) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      if (!intent) {
        const current = await getIncoming(document.id);
        if (!active()) return;
        if (current.status === "accepted") {
          clearConfirmed();
          setStorageInvalid(false);
        } else
          setError(
            "Документ ещё не принят. Для проверки сохранённого способа обратитесь к администратору.",
          );
      } else if (!(await reconcile(intent)) && active())
        setError("Документ ещё в черновике. Можно повторить приёмку тем же способом.");
    } catch (failure) {
      if (active())
        setError(
          failure instanceof IncomingAcceptanceError
            ? failure.message
            : describeApiError(failure, "Не удалось проверить статус. Выбранный способ сохранён."),
        );
    } finally {
      busyRef.current = false;
      if (active()) setBusy(false);
    }
  };
  return (
    <>
      {storageInvalid && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-danger/30 p-3 text-sm text-danger"
        >
          <p>
            Не удалось прочитать сохранённый результат приёмки. Не принимайте документ повторно;
            обратитесь к администратору для проверки.
          </p>
          <Button variant="secondary" size="sm" isLoading={busy} onClick={() => void check()}>
            Проверить документ
          </Button>
          {error && <p>{error}</p>}
        </div>
      )}
      {intent && !open && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm"
        >
          <p>
            Результат приёмки требует проверки. Изменение документа и выбор другого способа временно
            недоступны.
          </p>
          <Button variant="secondary" size="sm" isLoading={busy} onClick={() => void check()}>
            Проверить результат приёмки
          </Button>
          {error && <p>{error}</p>}
        </div>
      )}
      <ConfirmDialog
        open={open && !storageInvalid}
        title="Принять товары на склад"
        confirmLabel={intent ? "Повторить тем же способом" : "Принять на склад"}
        isLoading={busy}
        cancelDisabled={busy}
        onConfirm={() => void submit()}
        onCancel={onClose}
        message={
          <>
            <fieldset disabled={busy || intent !== null} className="space-y-3">
              <legend className="mb-2 font-medium">Как учитывать поступившие товары?</legend>
              <label className="block">
                <input type="radio" value="legacy_pack" {...form.register("stock_mode")} /> Обычная
                приёмка упаковок
              </label>
              <label className="block">
                <input
                  type="radio"
                  value="physical_unit_v2"
                  disabled={!document.physical_receiving_ready}
                  {...form.register("stock_mode")}
                />{" "}
                Упаковки, блистеры и поштучно
              </label>
            </fieldset>
            <p className="mt-3">
              {(intent?.stock_mode ?? mode) === "physical_unit_v2"
                ? "На склад поступят целые упаковки с сохранённым составом. При продаже можно будет выбирать упаковку, блистер или штуки."
                : "Товары поступят на склад с обычным учётом упаковок."}
            </p>
            {!document.physical_receiving_ready && (
              <p className="mt-2">
                Для поштучного учёта сохраните состав всех упаковок в позициях и укажите целое
                количество упаковок.
              </p>
            )}
            <p className="mt-2 font-medium">
              После принятия документ и его позиции нельзя изменить.
            </p>
            {intent && (
              <p className="mt-2">
                Способ уже сохранён:{" "}
                {intent.stock_mode === "physical_unit_v2"
                  ? "упаковки, блистеры и поштучно"
                  : "обычная приёмка"}
                . Перед повтором система проверит результат.
              </p>
            )}
            {error && (
              <p role="alert" className="mt-2 text-danger">
                {error}
              </p>
            )}
          </>
        }
      />
    </>
  );
}
