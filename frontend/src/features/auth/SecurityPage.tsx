import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  PageHeader,
  SkeletonRows,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { useActiveSessionsQuery, useRevokeActiveSession, useRevokeOtherSessions } from "./queries";
import { type ActiveSession } from "./types";

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function describeDevice(userAgent: string | null): string {
  if (!userAgent) return "Неизвестное устройство";

  const platform = /android/i.test(userAgent)
    ? "Android"
    : /iphone|ipad|ios/i.test(userAgent)
      ? "iPhone / iPad"
      : /windows/i.test(userAgent)
        ? "Windows"
        : /macintosh|mac os/i.test(userAgent)
          ? "macOS"
          : /linux/i.test(userAgent)
            ? "Linux"
            : "Другое устройство";
  const browser = /edg\//i.test(userAgent)
    ? "Edge"
    : /chrome\//i.test(userAgent)
      ? "Chrome"
      : /firefox\//i.test(userAgent)
        ? "Firefox"
        : /safari\//i.test(userAgent) && !/chrome\//i.test(userAgent)
          ? "Safari"
          : null;

  return browser ? `${platform} · ${browser}` : platform;
}

export function SecurityPage({ embedded = false }: { embedded?: boolean } = {}): JSX.Element {
  const sessionsQuery = useActiveSessionsQuery();
  const revokeSession = useRevokeActiveSession();
  const revokeOthers = useRevokeOtherSessions();
  const [sessionToRevoke, setSessionToRevoke] = useState<ActiveSession | null>(null);
  const [confirmOthers, setConfirmOthers] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const sessions = sessionsQuery.data ?? [];
  const otherSessions = sessions.filter((session) => !session.is_current);
  const isMutating = revokeSession.isPending || revokeOthers.isPending;

  const closeDialogs = () => {
    if (isMutating) return;
    setSessionToRevoke(null);
    setConfirmOthers(false);
  };

  const confirmSessionRevoke = () => {
    if (!sessionToRevoke) return;
    setActionError(null);
    setSuccessMessage(null);
    revokeSession.mutate(sessionToRevoke.id, {
      onSuccess: () => {
        setSessionToRevoke(null);
        setSuccessMessage("Сеанс завершён.");
      },
      onError: (error) => {
        setActionError(describeApiError(error, "Не удалось завершить сеанс"));
      },
    });
  };

  const confirmOtherRevoke = () => {
    setActionError(null);
    setSuccessMessage(null);
    revokeOthers.mutate(undefined, {
      onSuccess: (result) => {
        setConfirmOthers(false);
        setSuccessMessage(
          result.revoked_count > 0
            ? `Завершено сеансов: ${result.revoked_count}.`
            : "Других активных сеансов нет.",
        );
      },
      onError: (error) => {
        setActionError(describeApiError(error, "Не удалось завершить другие сеансы"));
      },
    });
  };

  if (sessionsQuery.isLoading) {
    return (
      <div className={embedded ? "space-y-5" : "max-w-4xl space-y-5"}>
        {!embedded ? <PageHeader title="Безопасность" /> : null}
        <Card>
          <CardContent>
            <SkeletonRows rows={3} />
          </CardContent>
        </Card>
      </div>
    );
  }

  if (sessionsQuery.error) {
    return (
      <div className={embedded ? "space-y-3" : "max-w-2xl space-y-3"}>
        {!embedded ? <PageHeader title="Безопасность" /> : null}
        <p className="text-sm text-danger">
          {describeApiError(sessionsQuery.error, "Не удалось загрузить активные сеансы")}
        </p>
        <Button type="button" variant="secondary" onClick={() => void sessionsQuery.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }

  return (
    <div className={embedded ? "space-y-5" : "max-w-4xl space-y-5"} data-testid="security-page">
      {embedded ? (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
          <div>
            <h2 className="font-display text-xl font-semibold text-foreground">Безопасность</h2>
            <p className="mt-1 text-sm text-foreground-muted">Активные сеансы аккаунта</p>
          </div>
          <Button
            type="button"
            variant="secondary"
            disabled={otherSessions.length === 0}
            onClick={() => {
              setActionError(null);
              setSuccessMessage(null);
              setConfirmOthers(true);
            }}
          >
            Завершить остальные
          </Button>
        </div>
      ) : (
        <PageHeader
          title="Безопасность"
          description="Активные сеансы аккаунта"
          actions={
            <Button
              type="button"
              variant="secondary"
              disabled={otherSessions.length === 0}
              onClick={() => {
                setActionError(null);
                setSuccessMessage(null);
                setConfirmOthers(true);
              }}
            >
              Завершить остальные
            </Button>
          }
        />
      )}

      {actionError && <p className="text-sm text-danger">{actionError}</p>}
      {successMessage && (
        <p className="text-sm text-success-foreground" role="status">
          {successMessage}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Сеансы ({sessions.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {sessions.length === 0 ? (
            <p className="px-6 py-8 text-sm text-foreground-muted">Активных сеансов нет.</p>
          ) : (
            <div className="divide-y divide-border">
              {sessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  disabled={isMutating}
                  onRevoke={() => setSessionToRevoke(session)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={sessionToRevoke !== null}
        title="Завершить сеанс?"
        message={
          sessionToRevoke
            ? `${describeDevice(sessionToRevoke.user_agent)} · последняя активность ${formatDate(sessionToRevoke.last_used_at)}`
            : ""
        }
        confirmLabel="Завершить"
        variant="danger"
        isLoading={revokeSession.isPending}
        onConfirm={confirmSessionRevoke}
        onCancel={closeDialogs}
      />

      <ConfirmDialog
        open={confirmOthers}
        title="Завершить остальные сеансы?"
        message="Текущий сеанс останется активным. На остальных устройствах потребуется войти снова."
        confirmLabel="Завершить остальные"
        variant="danger"
        isLoading={revokeOthers.isPending}
        onConfirm={confirmOtherRevoke}
        onCancel={closeDialogs}
      />
    </div>
  );
}

function SessionRow({
  session,
  disabled,
  onRevoke,
}: {
  session: ActiveSession;
  disabled: boolean;
  onRevoke: () => void;
}): JSX.Element {
  return (
    <div className="grid gap-4 px-4 py-4 sm:px-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate font-medium text-foreground">
            {describeDevice(session.user_agent)}
          </p>
          {session.is_current && <Badge tone="success">Текущий сеанс</Badge>}
        </div>
        <dl className="grid gap-x-5 gap-y-1 text-xs text-foreground-muted sm:grid-cols-3">
          <div>
            <dt className="inline">Последняя активность: </dt>
            <dd className="inline text-foreground-secondary">{formatDate(session.last_used_at)}</dd>
          </div>
          <div>
            <dt className="inline">Создан: </dt>
            <dd className="inline text-foreground-secondary">{formatDate(session.created_at)}</dd>
          </div>
          <div>
            <dt className="inline">Сеть: </dt>
            <dd className="inline text-foreground-secondary">{session.ip_address ?? "-"}</dd>
          </div>
        </dl>
      </div>
      <div className="flex items-center justify-between gap-3 md:justify-end">
        <span className="text-xs text-foreground-muted">До {formatDate(session.expires_at)}</span>
        {!session.is_current && (
          <Button type="button" variant="danger" size="sm" disabled={disabled} onClick={onRevoke}>
            Завершить
          </Button>
        )}
      </div>
    </div>
  );
}
