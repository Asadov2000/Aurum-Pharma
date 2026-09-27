import { useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { allChannels, channelAvailable, channelLabel, knownEvents } from "./labels";
import { usePatchSubscriptions, useSubscriptionsQuery } from "./queries";
import { type Channel, type Subscription } from "./types";

// Local form state: event_type → {enabled, channels-set}.
type Row = {
  is_enabled: boolean;
  channels: Set<Channel>;
};

function rowsFromServer(subs: Subscription[]): Map<string, Row> {
  const map = new Map<string, Row>();
  for (const s of subs) {
    map.set(s.event_type, {
      is_enabled: s.is_enabled,
      channels: new Set(s.channels),
    });
  }
  for (const e of knownEvents) {
    const current = map.get(e.key);
    if (e.mandatory) {
      map.set(e.key, {
        is_enabled: true,
        channels: new Set<Channel>(["in_app", ...(current?.channels ?? [])]),
      });
    } else if (!current) {
      // Backend default: ["in_app"] + enabled. Surface that to the user
      // so the visible state matches what the system will actually do.
      map.set(e.key, { is_enabled: true, channels: new Set<Channel>(["in_app"]) });
    }
  }
  return map;
}

export function SubscriptionsForm(): JSX.Element {
  const subs = useSubscriptionsQuery();
  const patch = usePatchSubscriptions();
  const [rows, setRows] = useState<Map<string, Row>>(new Map());
  const [topError, setTopError] = useState<string | null>(null);
  const [savedBanner, setSavedBanner] = useState(false);
  const dirty = useRef(false);

  useEffect(() => {
    if (subs.data && !dirty.current) setRows(rowsFromServer(subs.data));
  }, [subs.data]);

  const toggleEnabled = (key: string, on: boolean) => {
    dirty.current = true;
    setSavedBanner(false);
    setTopError(null);
    setRows((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? { is_enabled: true, channels: new Set(["in_app" as Channel]) };
      next.set(key, { ...cur, is_enabled: on });
      return next;
    });
  };

  const toggleChannel = (key: string, ch: Channel) => {
    dirty.current = true;
    setSavedBanner(false);
    setTopError(null);
    setRows((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? { is_enabled: true, channels: new Set(["in_app" as Channel]) };
      const channels = new Set(cur.channels);
      if (channels.has(ch)) channels.delete(ch);
      else channels.add(ch);
      next.set(key, { ...cur, channels });
      return next;
    });
  };

  const onSave = async () => {
    if (patch.isPending || subs.isLoading || subs.error || !subs.data || invalidChannels) return;
    setTopError(null);
    setSavedBanner(false);
    const items: Subscription[] = Array.from(rows.entries()).map(([event_type, r]) => ({
      event_type,
      channels: Array.from(r.channels),
      is_enabled: r.is_enabled,
    }));
    try {
      const saved = await patch.mutateAsync(items);
      dirty.current = false;
      setRows(rowsFromServer(saved));
      setSavedBanner(true);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить настройки уведомлений"));
    }
  };

  const ordered = useMemo(
    () =>
      knownEvents
        .map((e) => ({
          key: e.key,
          title: e.title,
          description: e.description,
          mandatory: e.mandatory === true,
          row: rows.get(e.key),
        }))
        .filter((x) => x.row !== undefined),
    [rows],
  );

  const invalidChannels = ordered.some(
    ({ row }) => row?.is_enabled && !Array.from(row.channels).some((ch) => channelAvailable[ch]),
  );

  if (subs.isLoading) return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  if (subs.error)
    return (
      <Card>
        <CardHeader>
          <CardTitle>Настройки уведомлений</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p role="alert" className="text-sm text-danger">
            {describeApiError(subs.error, "Не удалось загрузить настройки уведомлений")}
          </p>
          <Button
            variant="secondary"
            onClick={() => void subs.refetch()}
            disabled={subs.isFetching}
            isLoading={subs.isFetching}
          >
            Повторить загрузку
          </Button>
        </CardContent>
      </Card>
    );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Настройки уведомлений</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm leading-relaxed text-foreground-secondary">
          Выберите события и способы получения сообщений. Эти настройки относятся только к вашему
          аккаунту.
        </p>

        <div className="divide-y divide-border rounded-lg border border-border">
          {ordered.map(({ key, title, description, mandatory, row }) => {
            const r = row!;
            return (
              <section
                key={key}
                aria-labelledby={`notification-event-${key}`}
                className="space-y-3 p-4"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h3
                    id={`notification-event-${key}`}
                    className="text-sm font-semibold text-foreground"
                  >
                    {title}
                  </h3>
                  {mandatory && <Badge>Обязательно</Badge>}
                </div>
                <p className="text-sm leading-relaxed text-foreground-muted">{description}</p>
                {!mandatory && (
                  <div>
                    <Switch
                      label="Получать сообщения"
                      aria-label={`Получать сообщения: ${title}`}
                      checked={r.is_enabled}
                      onChange={(e) => toggleEnabled(key, e.target.checked)}
                      disabled={patch.isPending}
                    />
                  </div>
                )}
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {allChannels
                    .filter((ch) => channelAvailable[ch])
                    .map((ch) => {
                      return (
                        <Switch
                          key={ch}
                          label={channelLabel[ch]}
                          checked={r.channels.has(ch)}
                          onChange={() => toggleChannel(key, ch)}
                          disabled={mandatory || !r.is_enabled || patch.isPending}
                        />
                      );
                    })}
                </div>
                {mandatory && (
                  <p className="text-xs leading-relaxed text-foreground-muted">
                    Сообщения о безопасности обязательны и всегда приходят в систему.
                  </p>
                )}
              </section>
            );
          })}
        </div>

        {invalidChannels && (
          <p role="alert" className="text-sm text-danger">
            Для включённых сообщений выберите хотя бы один способ получения: В системе или Email.
          </p>
        )}
        {topError && (
          <p role="alert" className="text-sm text-danger">
            {topError}
          </p>
        )}
        {savedBanner && (
          <p
            className="rounded-lg border border-success/30 bg-success-subtle px-3 py-2 text-sm text-success-foreground"
            role="status"
          >
            Настройки уведомлений сохранены.
          </p>
        )}

        <Button
          onClick={() => void onSave()}
          isLoading={patch.isPending}
          disabled={patch.isPending || invalidChannels || !subs.data}
        >
          {topError ? "Повторить сохранение" : "Сохранить настройки"}
        </Button>
        <p className="text-xs leading-relaxed text-foreground-muted">
          SMS и Telegram для этих уведомлений пока недоступны. Настройки уведомлений не меняют
          способы входа в аккаунт.
        </p>
      </CardContent>
    </Card>
  );
}
