import { useState } from "react";

import {
  Badge,
  Button,
  Label,
  Select,
  Switch,
  TableEmpty,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { knownEvents, severityLabel, severityOptions, severityTone } from "./labels";
import {
  useMarkAllRead,
  useMarkRead,
  useNotificationsQuery,
} from "./queries";
import { type Notification, type Severity } from "./types";

export function Inbox(): JSX.Element {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [severity, setSeverity] = useState<Severity | "">("");
  const [actionError, setActionError] = useState<string | null>(null);

  const { data, isLoading, error } = useNotificationsQuery({
    unread_only: unreadOnly,
    severity: severity || undefined,
    page: 1,
    page_size: 100,
  });
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();

  const unreadCount = (data ?? []).filter((n) => n.read_at === null).length;

  const onMarkAll = async () => {
    setActionError(null);
    try {
      await markAll.mutateAsync();
    } catch (err) {
      setActionError(describeApiError(err, "Не удалось отметить"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface p-3">
        <Switch
          label="Только непрочитанные"
          checked={unreadOnly}
          onChange={(e) => setUnreadOnly(e.target.checked)}
        />
        <div>
          <Label htmlFor="severity">Уровень</Label>
          <Select
            id="severity"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as Severity | "")}
            className="w-52"
          >
            <option value="">Все</option>
            {severityOptions.map((s) => (
              <option key={s} value={s}>
                {severityLabel[s]}
              </option>
            ))}
          </Select>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-sm text-foreground-muted">
            непрочитанных: <span className="font-mono">{unreadCount}</span>
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void onMarkAll()}
            disabled={unreadCount === 0}
            isLoading={markAll.isPending}
          >
            Отметить все
          </Button>
        </div>
      </div>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить уведомления")}
        </p>
      )}
      {actionError && <p className="text-sm text-danger">{actionError}</p>}

      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>
          {unreadOnly || severity ? "По фильтрам ничего нет" : "Пока нет уведомлений"}
        </TableEmpty>
      ) : (
        <ul className="space-y-2">
          {data.map((n) => (
            <Item
              key={n.id}
              n={n}
              onMark={() => void markRead.mutateAsync(n.id).catch(() => {})}
              isPending={markRead.isPending}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Item({
  n,
  onMark,
  isPending,
}: {
  n: Notification;
  onMark: () => void;
  isPending: boolean;
}): JSX.Element {
  const isUnread = n.read_at === null;
  const eventTitle = knownEvents.find((e) => e.key === n.event_type)?.title ?? n.event_type;
  return (
    <li
      className={cn(
        "rounded-md border px-4 py-3",
        isUnread ? "border-input bg-surface" : "border-border bg-foreground/[0.03]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <Badge tone={severityTone[n.severity]}>{severityLabel[n.severity]}</Badge>
            <span className="text-xs text-foreground-muted">{eventTitle}</span>
            {isUnread && <Badge tone="info">новое</Badge>}
          </div>
          <p className={cn("text-sm", isUnread ? "font-medium text-foreground" : "text-foreground-secondary")}>
            {n.title}
          </p>
          {n.body && <p className="text-sm text-foreground-secondary">{n.body}</p>}
          <p className="text-xs text-foreground-muted">
            {new Date(n.created_at).toLocaleString("ru-RU")}
          </p>
        </div>
        {isUnread && (
          <Button variant="ghost" size="sm" onClick={onMark} isLoading={isPending}>
            Прочитано
          </Button>
        )}
      </div>
    </li>
  );
}
