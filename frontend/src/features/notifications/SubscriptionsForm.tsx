import { useEffect, useMemo, useState } from "react";

import { Button, Card, CardContent, CardHeader, CardTitle, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import {
  allChannels,
  channelAvailable,
  channelLabel,
  knownEvents,
} from "./labels";
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
    if (!map.has(e.key)) {
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

  useEffect(() => {
    if (subs.data) setRows(rowsFromServer(subs.data));
  }, [subs.data]);

  const toggleEnabled = (key: string, on: boolean) => {
    setRows((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? { is_enabled: true, channels: new Set(["in_app" as Channel]) };
      next.set(key, { ...cur, is_enabled: on });
      return next;
    });
  };

  const toggleChannel = (key: string, ch: Channel) => {
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
    setTopError(null);
    setSavedBanner(false);
    const items: Subscription[] = Array.from(rows.entries()).map(([event_type, r]) => ({
      event_type,
      channels: Array.from(r.channels),
      is_enabled: r.is_enabled,
    }));
    try {
      await patch.mutateAsync(items);
      setSavedBanner(true);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить подписки"));
    }
  };

  const ordered = useMemo(
    () =>
      knownEvents
        .map((e) => ({
          key: e.key,
          title: e.title,
          description: e.description,
          row: rows.get(e.key),
        }))
        .filter((x) => x.row !== undefined),
    [rows],
  );

  if (subs.isLoading) return <p className="text-sm text-slate-500">Загрузка…</p>;
  if (subs.error)
    return (
      <p className="text-sm text-red-600">
        {describeApiError(subs.error, "Не удалось загрузить подписки")}
      </p>
    );

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Выберите, какие события и по каким каналам вы хотите получать. По умолчанию все
        события приходят в систему.
      </p>

      <div className="space-y-3">
        {ordered.map(({ key, title, description, row }) => {
          const r = row!;
          return (
            <Card key={key}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">{title}</CardTitle>
                    <p className="mt-1 text-xs text-slate-500">{description}</p>
                  </div>
                  <Switch
                    label={r.is_enabled ? "Включено" : "Выключено"}
                    checked={r.is_enabled}
                    onChange={(e) => toggleEnabled(key, e.target.checked)}
                  />
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-4">
                  {allChannels.map((ch) => {
                    const available = channelAvailable[ch];
                    return (
                      <Switch
                        key={ch}
                        label={
                          available
                            ? channelLabel[ch]
                            : `${channelLabel[ch]} (Этап 2)`
                        }
                        checked={r.channels.has(ch)}
                        onChange={() => toggleChannel(key, ch)}
                        disabled={!available || !r.is_enabled}
                      />
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {topError && <p className="text-sm text-red-600">{topError}</p>}
      {savedBanner && <p className="text-sm text-emerald-700">✅ Подписки сохранены.</p>}

      <Button onClick={() => void onSave()} isLoading={patch.isPending}>
        Сохранить
      </Button>
    </div>
  );
}
