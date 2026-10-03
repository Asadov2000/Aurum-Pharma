import { Link } from "@tanstack/react-router";
import { ContextHelpPortal } from "@/components/layout/ContextHelpPortal";

import { Card, CardContent, PageHeader } from "@/components/ui";

import { Inbox } from "./Inbox";
import { SubscriptionsForm } from "./SubscriptionsForm";

export function NotificationsPage(): JSX.Element {
  return (
    <div className="mx-auto w-full max-w-7xl space-y-5">
      <ContextHelpPortal pathname="/notifications" />
      <PageHeader
        title="Уведомления"
        description="Сообщения для вас и настройки их получения."
        showTitleOnDesktop
        actions={
          <Link
            to="/profile"
            className="inline-flex min-h-10 items-center rounded-lg border border-input bg-surface px-4 text-sm font-semibold text-primary hover:bg-primary/5"
          >
            Мой профиль
          </Link>
        }
      />
      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Inbox />
        <aside className="min-w-0 space-y-5" aria-label="Получение уведомлений">
          <SubscriptionsForm />
          <Card>
            <CardContent className="space-y-3">
              <h2 className="text-sm font-semibold text-foreground">Сообщения безопасности</h2>
              <p className="text-sm leading-relaxed text-foreground-muted">
                Уведомления о новом устройстве помогают заметить незнакомый вход. Их нельзя
                отключить.
              </p>
              <Link
                to="/security"
                className="inline-flex min-h-10 items-center text-sm font-semibold text-primary hover:underline"
              >
                Открыть безопасность
              </Link>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
