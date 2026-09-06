import { useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Label,
  Select,
  Switch,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { knownEvents, severityLabel, severityOptions, severityTone } from "./labels";
import { useMarkAllRead, useMarkRead, useNotificationsQuery } from "./queries";
import { type Notification, type Severity } from "./types";

export function Inbox(): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("notifications");
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
      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "unread",
            label: "Только непрочитанные",
            content: (
              <div className="flex h-10 items-center">
                <Switch
                  label="Только непрочитанные"
                  checked={unreadOnly}
                  onChange={(e) => setUnreadOnly(e.target.checked)}
                />
              </div>
            ),
            active: unreadOnly,
            onClear: () => setUnreadOnly(false),
            defaultVisible: true,
          },
          {
            id: "severity",
            label: "Уровень",
            content: (
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
            ),
            active: Boolean(severity),
            activeLabel: severity ? `Уровень: ${severityLabel[severity]}` : undefined,
            onClear: () => setSeverity(""),
            defaultVisible: true,
          },
        ]}
        onResetValues={() => {
          setUnreadOnly(false);
          setSeverity("");
        }}
        actions={
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
        }
      />

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
  const isNewDeviceAlert = n.event_type === "security.new_device_login";
  const eventTitle = knownEvents.find((e) => e.key === n.event_type)?.title ?? n.event_type;
  return (
    <li
      className={cn(
        "rounded-md border px-4 py-3",
        isUnread ? "border-input bg-surface" : "border-border bg-foreground/[0.03]",
      )}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={severityTone[n.severity]}>{severityLabel[n.severity]}</Badge>
            {eventTitle !== n.title && (
              <span className="text-xs text-foreground-muted">{eventTitle}</span>
            )}
            {isUnread && <Badge tone="info">новое</Badge>}
          </div>
          <p
            className={cn(
              "text-sm",
              isUnread ? "font-medium text-foreground" : "text-foreground-secondary",
            )}
          >
            {n.title}
          </p>
          {n.body && <p className="text-sm text-foreground-secondary">{n.body}</p>}
          <p className="text-xs text-foreground-muted">
            {new Date(n.created_at).toLocaleString("ru-RU")}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center justify-end gap-1 sm:w-auto sm:shrink-0">
          {isNewDeviceAlert && (
            <Link
              to="/security"
              className="inline-flex h-8 items-center justify-center rounded-md border border-input bg-surface px-3 text-sm font-semibold text-foreground transition-colors duration-fast hover:bg-foreground/5"
            >
              Проверить сеансы
            </Link>
          )}
          {isUnread && (
            <Button variant="ghost" size="sm" onClick={onMark} isLoading={isPending}>
              Прочитано
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}
