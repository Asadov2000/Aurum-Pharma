import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { NavIcon } from "@/components/layout/icons";
import { ContextHelpPortal } from "@/components/layout/ContextHelpPortal";
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
import { OwnershipTransferPanel } from "@/features/roles/OwnershipTransferPanel";

import { useActiveSessionsQuery } from "./queries";
import { useRevokeActiveSession, useRevokeOtherSessions } from "./sessionMutationQueries";
import { type ActiveSession } from "./types";
import { MfaSettingsPanel } from "./MfaSettingsPanel";
import { AccountIdentityPanel } from "./AccountIdentityPanel";

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
  return (
    <div
      className={embedded ? "w-full space-y-5" : "mx-auto w-full max-w-6xl space-y-5"}
      data-testid="security-page"
    >
      {!embedded && <ContextHelpPortal pathname="/security" />}
      {embedded ? (
        <div>
          <h2 className="font-display text-xl font-semibold text-foreground">Безопасность</h2>
          <p className="mt-1 text-sm text-foreground-muted">
            Способы входа, дополнительная защита и ваши устройства.
          </p>
        </div>
      ) : (
        <PageHeader
          title="Безопасность"
          showTitleOnDesktop
          description="Способы входа, дополнительная защита и ваши устройства."
          actions={
            <Link
              to="/profile"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            >
              <NavIcon to="/profile" />
              Мой профиль
            </Link>
          }
        />
      )}
      <section
        className="flex flex-wrap items-center gap-4 rounded-xl border border-primary/20 bg-primary/[0.05] p-5 sm:p-6"
        aria-labelledby="security-overview-heading"
      >
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-surface text-primary">
          <NavIcon to="/security" />
        </span>
        <div className="min-w-0 flex-1 basis-64">
          <h2 id="security-overview-heading" className="font-semibold text-foreground">
            Защита вашего аккаунта
          </h2>
          <p className="mt-1 text-sm text-foreground-secondary">
            Проверьте способы входа и устройства. Дополнительный код из приложения подключается по
            вашему желанию.
          </p>
        </div>
        <a
          href="#security-mfa"
          className="inline-flex min-h-11 items-center justify-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
        >
          Открыть настройки защиты
        </a>
      </section>
      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
          <AccountIdentityPanel presentation="security" />
          <SessionsPanel />
          <OwnershipTransferPanel />
        </div>
        <div className="min-w-0 space-y-5">
          <section
            id="security-mfa"
            className="scroll-mt-24"
            aria-label="Настройки дополнительной защиты"
          >
            <MfaSettingsPanel presentation="security" />
          </section>
          <Card>
            <CardHeader>
              <CardTitle>Не узнаёте устройство?</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-foreground-secondary">
              <p>
                Завершите его сеанс. Если вы не выполняли этот вход, восстановите пароль и проверьте
                способы входа.
              </p>
              <details>
                <summary className="min-h-11 cursor-pointer py-3 font-medium text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  Что делать
                </summary>
                <p className="mt-2">
                  На странице входа нажмите «Забыли пароль?» и подтвердите свою личность. Проверьте
                  номер телефона в своём профиле и при необходимости включите дополнительную защиту.
                </p>
              </details>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function SessionsPanel(): JSX.Element {
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
    if (!sessionToRevoke || sessionToRevoke.is_current || isMutating || sessionsQuery.error) return;
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
    if (isMutating || sessionsQuery.error) return;
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
      <Card>
        <CardHeader>
          <CardTitle>Устройства и входы</CardTitle>
        </CardHeader>
        <CardContent>
          <SkeletonRows rows={3} />
        </CardContent>
      </Card>
    );
  }

  if (sessionsQuery.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Устройства и входы</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p role="alert" className="text-sm text-danger">
            {describeApiError(sessionsQuery.error, "Не удалось загрузить активные сеансы")}
          </p>
          <Button type="button" variant="secondary" onClick={() => void sessionsQuery.refetch()}>
            Повторить
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="min-w-0 space-y-4">
      <Card className="min-w-0">
        <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>Устройства и входы</CardTitle>
            <Badge tone="neutral">Сеансы ({sessions.length})</Badge>
          </div>
          <Button
            type="button"
            variant="secondary"
            disabled={otherSessions.length === 0 || isMutating}
            onClick={() => {
              setActionError(null);
              setSuccessMessage(null);
              setConfirmOthers(true);
            }}
          >
            Завершить остальные
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-foreground-muted">
            Здесь видно, где выполнен вход в ваш аккаунт. Незнакомый сеанс можно завершить.
          </p>
          <Button
            variant="secondary"
            size="sm"
            disabled={sessionsQuery.isFetching || isMutating}
            onClick={() => void sessionsQuery.refetch()}
          >
            {sessionsQuery.isFetching ? "Обновление…" : "Обновить устройства"}
          </Button>
          {actionError && (
            <p role="alert" className="text-sm text-danger">
              {actionError}
            </p>
          )}
          {successMessage && (
            <p className="text-sm text-success-foreground" role="status">
              {successMessage}
            </p>
          )}

          {sessions.length === 0 ? (
            <p className="px-6 py-8 text-sm text-foreground-muted">Активных сеансов нет.</p>
          ) : (
            <ul className="space-y-3" aria-label="Активные сеансы">
              {sessions.map((session) => (
                <li key={session.id}>
                  <SessionRow
                    session={session}
                    disabled={isMutating}
                    onRevoke={() => setSessionToRevoke(session)}
                  />
                </li>
              ))}
            </ul>
          )}
          <p className="border-t border-border pt-3 text-xs text-foreground-muted">
            Текущий сеанс останется открытым. Время указано по часовому поясу этого устройства.
          </p>
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
    <article
      className={`grid min-w-0 gap-4 rounded-xl border px-4 py-4 sm:px-5 ${session.is_current ? "border-primary/20 bg-primary/[0.04]" : "border-border"}`}
    >
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="break-words font-medium text-foreground">
            {describeDevice(session.user_agent)}
          </p>
          {session.is_current && <Badge tone="success">Текущий сеанс</Badge>}
        </div>
        <dl className="grid gap-x-5 gap-y-2 text-xs text-foreground-muted sm:grid-cols-2">
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
            <dd className="inline break-all text-foreground-secondary">
              {session.ip_address ?? "-"}
            </dd>
          </div>
        </dl>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-foreground-muted">До {formatDate(session.expires_at)}</span>
        {!session.is_current && (
          <Button type="button" variant="danger" size="sm" disabled={disabled} onClick={onRevoke}>
            Завершить
          </Button>
        )}
      </div>
    </article>
  );
}
