import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, FormError, Input, Label, Modal, Pagination } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { createOperationId } from "@/lib/operationId";

import { payDate, payDateTime, payMoney } from "./format";
import {
  useStaffPayAccess,
  useStaffPayCommands,
  useStaffPayEntries,
  useStaffPayRevisions,
} from "./queries";
import { type StaffPayEntry } from "./types";

export function PayHistory({
  own,
  periodId,
  mode,
  onClose,
}: {
  own: boolean;
  periodId: string;
  mode: "entries" | "revisions";
  onClose: () => void;
}): JSX.Element {
  return (
    <Modal open onClose={onClose} title={mode === "entries" ? "История выдач" : "Изменения суммы"}>
      {mode === "entries" ? (
        <EntryHistory own={own} periodId={periodId} />
      ) : (
        <RevisionHistory own={own} periodId={periodId} />
      )}
    </Modal>
  );
}

function EntryHistory({ own, periodId }: { own: boolean; periodId: string }): JSX.Element {
  const [page, setPage] = useState(1);
  const query = useStaffPayEntries(own, periodId, page);
  const access = useStaffPayAccess();
  const [reversing, setReversing] = useState<StaffPayEntry | null>(null);
  if (query.isPending) return <p role="status">Загрузка истории…</p>;
  if (query.isError) return <ReadError error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return <></>;
  return (
    <div className="space-y-4">
      {query.data.items.length === 0 ? (
        <p>Выдач пока нет.</p>
      ) : (
        <ul className="space-y-3" aria-label="Выдачи наличными">
          {query.data.items.map((entry) => (
            <li key={entry.id} className="space-y-2 rounded-md border border-border p-3">
              <div className="flex flex-wrap justify-between gap-2">
                <strong>
                  {entry.kind === "advance"
                    ? "Аванс"
                    : entry.kind === "payment"
                      ? "Выплата"
                      : "Отмена выдачи"}
                </strong>
                <span>
                  {entry.kind === "reversal" ? "−" : ""}
                  {payMoney(entry.amount)}
                </span>
              </div>
              <p className="text-sm text-foreground-muted">{payDate(entry.paid_on)}</p>
              {entry.is_reversed ? <Badge tone="warning">Отменена</Badge> : null}
              {entry.note ? <p className="break-words text-sm">{entry.note}</p> : null}
              {!own && access.manage && entry.kind !== "reversal" && !entry.is_reversed ? (
                <Button variant="secondary" onClick={() => setReversing(entry)}>
                  Отменить запись
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <Pagination
        page={page}
        pageSize={query.data.page_size}
        total={query.data.total}
        onPage={setPage}
      />
      {reversing ? (
        <ReverseForm
          key={reversing.id}
          periodId={periodId}
          entry={reversing}
          onClose={() => setReversing(null)}
        />
      ) : null}
    </div>
  );
}

function RevisionHistory({ own, periodId }: { own: boolean; periodId: string }): JSX.Element {
  const [page, setPage] = useState(1);
  const query = useStaffPayRevisions(own, periodId, page);
  if (query.isPending) return <p role="status">Загрузка изменений…</p>;
  if (query.isError) return <ReadError error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return <></>;
  return (
    <div className="space-y-4">
      <ul className="space-y-3" aria-label="Изменения суммы за период">
        {query.data.items.map((revision) => (
          <li key={revision.id} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex flex-wrap justify-between gap-2">
              <strong>{revision.row_version === 1 ? "Первичная запись" : "Изменено"}</strong>
              <span>{payMoney(revision.amount)}</span>
            </div>
            <p className="text-sm text-foreground-muted">
              {payDateTime(revision.created_at, query.data.timezone)}
            </p>
            {revision.note ? <p className="break-words text-sm">{revision.note}</p> : null}
          </li>
        ))}
      </ul>
      <Pagination
        page={page}
        pageSize={query.data.page_size}
        total={query.data.total}
        onPage={setPage}
      />
    </div>
  );
}

const reasonSchema = z
  .string()
  .trim()
  .min(3, "Укажите причину, не менее 3 символов")
  .max(1000, "Не более 1000 символов");
function ReverseForm({
  periodId,
  entry,
  onClose,
}: {
  periodId: string;
  entry: StaffPayEntry;
  onClose: () => void;
}): JSX.Element {
  const commands = useStaffPayCommands();
  const form = useForm<{ reason: string }>({ defaultValues: { reason: "" } });
  const operationId = useRef(createOperationId());
  const submitted = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const pending = commands.reverse.isPending;
  const submit = form.handleSubmit(async ({ reason }) => {
    const result = reasonSchema.safeParse(submitted.current ?? reason);
    if (!result.success) {
      form.setError("reason", { message: result.error.issues[0]?.message });
      return;
    }
    submitted.current = result.data;
    setLocked(true);
    setError(null);
    try {
      await commands.reverse.mutateAsync({
        periodId,
        entryId: entry.id,
        payload: { reason: result.data, operation_id: operationId.current },
      });
      onClose();
    } catch (cause) {
      setError(
        describeApiError(
          cause,
          "Проверьте историю перед новой отменой или повторите тот же запрос.",
        ),
      );
    }
  });
  return (
    <Modal
      open
      title="Отменить запись"
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form className="space-y-4" onSubmit={(event) => void submit(event)}>
        <p className="text-sm">
          Будет создана связанная отмена на {payMoney(entry.amount)}. Исходная запись останется в
          истории. Это исправление сведений, деньги не перемещаются.
        </p>
        <div>
          <Label htmlFor="pay-reason">Причина отмены</Label>
          <Input
            id="pay-reason"
            autoComplete="off"
            maxLength={1000}
            disabled={pending || locked}
            {...form.register("reason")}
          />
          <FormError>{form.formState.errors.reason?.message}</FormError>
        </div>
        <FormError>{error}</FormError>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            Закрыть
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Сохранение…" : error ? "Повторить запрос" : "Подтвердить отмену"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function ReadError({ error, retry }: { error: unknown; retry: () => void }): JSX.Element {
  return (
    <div className="space-y-3">
      <FormError>
        {describeApiError(
          error,
          "Не удалось загрузить сведения. При отсутствии интернета данные недоступны.",
        )}
      </FormError>
      <Button variant="secondary" onClick={retry}>
        Повторить
      </Button>
    </div>
  );
}
