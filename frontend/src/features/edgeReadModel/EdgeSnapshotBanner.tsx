import { useMemo } from "react";

import { resolveDataPlane } from "./dataPlane";
import { useEdgeSnapshotMetadata } from "./snapshotStore";

export function EdgeSnapshotBanner(): JSX.Element | null {
  const plane = useMemo(() => resolveDataPlane(), []);
  const metadata = useEdgeSnapshotMetadata();
  if (plane.mode !== "edge-readonly") return null;

  const confirmed = metadata ? formatConfirmedAt(metadata.last_confirmed_at) : null;
  return (
    <div
      className={`border-b px-4 py-2 text-sm sm:px-6 ${
        metadata?.is_stale
          ? "border-warning/30 bg-warning-subtle text-warning-foreground"
          : "border-info/30 bg-info-subtle text-info-foreground"
      }`}
      role="status"
      aria-live="polite"
    >
      <span className="font-semibold">Локальные данные · только чтение.</span>{" "}
      {metadata ? (
        metadata.is_stale ? (
          <>Последнее подтверждение: {confirmed}. Данные Cloud устарели.</>
        ) : (
          <>Снимок подтверждён: {confirmed}. Изменения временно недоступны.</>
        )
      ) : (
        <>Свежесть будет показана после загрузки локального снимка.</>
      )}
    </div>
  );
}

function formatConfirmedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "неизвестно";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}
