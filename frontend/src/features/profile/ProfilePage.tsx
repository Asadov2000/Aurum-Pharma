import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  PageHeader,
  SkeletonRows,
} from "@/components/ui";
import { AccountIdentityPanel } from "@/features/auth/AccountIdentityPanel";
import { describeApiError } from "@/lib/errorMessages";

import { usePersonalProfileQuery } from "./queries";
import { type PersonalProfile } from "./types";

const personalLinks = [
  { to: "/security", label: "Безопасность аккаунта" },
  { to: "/settings", label: "Настройки интерфейса" },
  { to: "/notifications", label: "Уведомления" },
] as const;

export function ProfilePage(): JSX.Element {
  const profile = usePersonalProfileQuery();
  return (
    <div className="max-w-5xl space-y-5">
      <PageHeader
        title="Мой профиль"
        showTitleOnDesktop
        description="Ваши данные, способы входа и назначенный рабочий доступ."
      />
      <nav aria-label="Личные настройки" className="flex flex-wrap gap-2">
        {personalLinks.map((link) => (
          <Link
            key={link.to}
            to={link.to}
            className="inline-flex min-h-11 items-center rounded-md border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            {link.label}
          </Link>
        ))}
      </nav>
      {profile.isPending ? (
        <Card>
          <CardContent>
            <p role="status" className="mb-3 text-sm text-foreground-muted">
              Загрузка профиля…
            </p>
            <SkeletonRows rows={3} />
          </CardContent>
        </Card>
      ) : profile.isError ? (
        <Card>
          <CardContent className="space-y-3">
            <p role="alert" className="text-sm text-danger">
              {describeApiError(profile.error, "Не удалось загрузить профиль.")}
            </p>
            <Button variant="secondary" onClick={() => void profile.refetch()}>
              Повторить
            </Button>
          </CardContent>
        </Card>
      ) : profile.data ? (
        <div className="grid items-start gap-5 md:grid-cols-2">
          <PersonalDetails profile={profile.data} />
          <WorkAccess profile={profile.data} />
        </div>
      ) : null}
      <AccountIdentityPanel />
    </div>
  );
}

function PersonalDetails({ profile }: { profile: PersonalProfile }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Личные данные</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="space-y-4">
          <div>
            <dt className="text-sm text-foreground-muted">ФИО</dt>
            <dd className="mt-1 break-words font-medium text-foreground">
              {profile.full_name || "Не указано"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-foreground-muted">Почта для входа</dt>
            <dd className="mt-1 break-all text-foreground-secondary">{profile.email}</dd>
          </div>
          <div>
            <dt className="mb-1 text-sm text-foreground-muted">Состояние аккаунта</dt>
            <dd>
              <Badge tone={profile.status === "active" ? "success" : "warning"}>
                {profile.status === "active" ? "Активен" : "Требует внимания"}
              </Badge>
            </dd>
          </div>
        </dl>
        <p className="border-t border-border pt-4 text-sm text-foreground-muted">
          ФИО используется в рабочих документах. Если в нём ошибка, обратитесь к руководителю или
          администратору Aurum.
        </p>
      </CardContent>
    </Card>
  );
}

function WorkAccess({ profile }: { profile: PersonalProfile }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Рабочий доступ</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {profile.platform_role ? (
          <Badge>
            {profile.platform_role === "developer" ? "Разработчик Aurum" : "Администратор Aurum"}
          </Badge>
        ) : null}
        {profile.is_tenant_owner ? <Badge tone="success">Владелец аптеки</Badge> : null}
        {profile.access_assignments.length > 0 ? (
          <ul className="divide-y divide-border">
            {profile.access_assignments.map((assignment) => (
              <li
                key={`${assignment.scope}:${assignment.branch?.id ?? "tenant"}:${assignment.role_id}`}
                className="py-3 first:pt-0 last:pb-0"
              >
                <p className="break-words font-medium text-foreground">{assignment.role_name}</p>
                <p className="mt-1 break-words text-sm text-foreground-secondary">
                  {assignment.scope === "tenant" ? "Все точки аптеки" : assignment.branch?.name}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-foreground-secondary">
            {profile.tenant_id
              ? "Роли в этой аптеке пока не назначены."
              : "Назначений сотрудника аптеки в текущей рабочей области нет."}
          </p>
        )}
        <p className="border-t border-border pt-4 text-sm text-foreground-muted">
          Доступ определяется назначенными ролями и точками. Изменить его может руководитель или
          уполномоченный администратор.
        </p>
      </CardContent>
    </Card>
  );
}
