import { useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Label,
  SegmentedControl,
  Select,
  TableEmpty,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { knownEvents, severityLabel, severityOptions, severityTone } from "./labels";
import { useMarkAllRead, useMarkRead, useNotificationsQuery } from "./queries";
import { type Notification, type Severity } from "./types";

const PAGE_SIZE = 100;

export function Inbox(): JSX.Element {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [severity, setSeverity] = useState<Severity | "">("");
  const [page, setPage] = useState(1);
  const [actionError, setActionError] = useState<string | null>(null);
  const query = useNotificationsQuery({
    unread_only: unreadOnly,
    severity: severity || undefined,
    page,
    page_size: PAGE_SIZE,
  });
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const items = query.error || query.isPlaceholderData ? [] : (query.data ?? []);
  const unreadCount = items.filter((n) => n.read_at === null).length;
  const busy = markRead.isPending || markAll.isPending;
  const loading = query.isLoading || query.isPlaceholderData;

  const onMark = async (id: string) => {
    if (busy || query.error || loading) return;
    setActionError(null);
    try {
      await markRead.mutateAsync(id);
    } catch (err) {
      setActionError(describeApiError(err, "Не удалось отметить уведомление"));
    }
  };
  const onMarkAll = async () => {
    if (busy || query.error || loading || unreadCount === 0) return;
    setActionError(null);
    try {
      await markAll.mutateAsync();
    } catch (err) {
      setActionError(describeApiError(err, "Не удалось отметить"));
    }
  };

  return (
    <Card className="min-w-0" aria-labelledby="notifications-overview-heading">
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle id="notifications-overview-heading">Входящие</CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void query.refetch()}
          disabled={query.isFetching || busy}
          isLoading={query.isFetching}
        >
          Обновить
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <SegmentedControl
            label="Сообщения"
            value={unreadOnly ? "unread" : "all"}
            options={[
              { value: "all", label: "Все" },
              { value: "unread", label: "Непрочитанные" },
            ]}
            onChange={(value) => {
              setUnreadOnly(value === "unread");
              setPage(1);
              setActionError(null);
            }}
          />
          <div className="min-w-0 flex-1 sm:max-w-52">
            <Label htmlFor="notification-severity">Уровень</Label>
            <Select
              id="notification-severity"
              value={severity}
              onChange={(e) => {
                setSeverity(e.target.value as Severity | "");
                setPage(1);
                setActionError(null);
              }}
            >
              <option value="">Вся важность</option>
              {severityOptions.map((s) => (
                <option key={s} value={s}>
                  {severityLabel[s]}
                </option>
              ))}
            </Select>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void onMarkAll()}
            disabled={unreadCount === 0 || busy || loading || Boolean(query.error)}
            isLoading={markAll.isPending}
          >
            Отметить все
          </Button>
        </div>
        <div className="space-y-1 text-xs leading-relaxed text-foreground-muted">
          <p>
            В текущей подборке непрочитанных: {unreadCount}. Страница {page}.
          </p>
          <p>Время указано для Душанбе (UTC+5).</p>
          <p>
            «Отметить все» помечает прочитанными все ваши уведомления, включая скрытые фильтрами.
          </p>
        </div>
        {actionError && (
          <p role="alert" className="text-sm text-danger">
            {actionError}
          </p>
        )}
        {query.error ? (
          <div className="space-y-3">
            <p role="alert" className="text-sm text-danger">
              {describeApiError(query.error, "Не удалось загрузить уведомления")}
            </p>
            <Button
              variant="secondary"
              onClick={() => void query.refetch()}
              disabled={query.isFetching}
              isLoading={query.isFetching}
            >
              Повторить
            </Button>
          </div>
        ) : loading ? (
          <p role="status" className="text-sm text-foreground-muted">
            Загрузка…
          </p>
        ) : items.length === 0 ? (
          <TableEmpty>
            {page > 1
              ? "На этой странице уведомлений нет"
              : unreadOnly || severity
                ? "По фильтрам ничего нет"
                : "Пока нет уведомлений"}
          </TableEmpty>
        ) : (
          <ul className="space-y-3" aria-label="Входящие уведомления">
            {items.map((n) => (
              <Item key={n.id} n={n} onMark={() => void onMark(n.id)} isPending={busy} />
            ))}
          </ul>
        )}
        {(page > 1 || items.length === PAGE_SIZE) && !query.error && (
          <nav className="flex flex-wrap gap-2" aria-label="Страницы уведомлений">
            <Button
              variant="secondary"
              size="sm"
              disabled={page === 1 || loading || busy}
              onClick={() => setPage((current) => current - 1)}
            >
              Назад
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={items.length < PAGE_SIZE || loading || busy}
              onClick={() => setPage((current) => current + 1)}
            >
              Далее
            </Button>
          </nav>
        )}
      </CardContent>
    </Card>
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
        "min-w-0 rounded-lg border p-4",
        isUnread
          ? "border-primary/20 border-l-[3px] border-l-primary bg-primary/5"
          : "border-border bg-surface",
      )}
    >
      <div className="min-w-0 space-y-3 break-words">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={severityTone[n.severity]}>{severityLabel[n.severity]}</Badge>
          <time dateTime={n.created_at} className="text-xs text-foreground-muted">
            {new Date(n.created_at).toLocaleString("ru-RU", {
              dateStyle: "short",
              timeStyle: "short",
              timeZone: "Asia/Dushanbe",
            })}
          </time>
          {isUnread && <Badge tone="info">новое</Badge>}
        </div>
        <div className="space-y-1">
          <p
            className={cn(
              "text-sm",
              isUnread ? "font-semibold text-foreground" : "text-foreground-secondary",
            )}
          >
            {n.title}
          </p>
          {eventTitle !== n.title && <p className="text-xs text-foreground-muted">{eventTitle}</p>}
          {n.body && <p className="text-sm leading-relaxed text-foreground-secondary">{n.body}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isNewDeviceAlert && (
            <Link
              to="/security"
              className="inline-flex min-h-10 items-center rounded-md px-2 text-sm font-semibold text-primary hover:bg-primary/5"
            >
              Проверить сеансы
            </Link>
          )}
          {isUnread ? (
            <Button variant="ghost" size="sm" onClick={onMark} disabled={isPending}>
              Прочитано
            </Button>
          ) : (
            <span className="text-xs text-foreground-muted">Прочитано</span>
          )}
        </div>
      </div>
    </li>
  );
}
