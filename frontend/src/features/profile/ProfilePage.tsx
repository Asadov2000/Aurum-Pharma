import { Link } from "@tanstack/react-router";

import { NavIcon } from "@/components/layout/icons";
import { canAccessPath, getRouteAccessContext } from "@/components/layout/routeAccess";
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
import { useAuthStore } from "@/stores/auth";

import { usePersonalProfileQuery } from "./queries";
import { type PersonalProfile } from "./types";

const personalLinks = [
  { to: "/security", label: "Безопасность аккаунта" },
  { to: "/settings", label: "Настройки интерфейса" },
  { to: "/notifications", label: "Уведомления" },
] as const;

const personalLinkClass =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary";

export function ProfilePage(): JSX.Element {
  const profile = usePersonalProfileQuery();
  const user = useAuthStore((state) => state.user);
  const visibleProfile = !profile.isPending && !profile.isError ? profile.data : undefined;
  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader
        title="Мой профиль"
        showTitleOnDesktop
        description="Личные данные, способы входа и рабочий доступ."
        actions={
          canAccessPath("/my-work", getRouteAccessContext(user)) ? (
            <Link to="/my-work" className={personalLinkClass}>
              <NavIcon to="/my-work" />
              Моя работа
              <ArrowIcon />
            </Link>
          ) : undefined
        }
      />
      {visibleProfile && <ProfileIdentity profile={visibleProfile} />}
      <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
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
          ) : visibleProfile ? (
            <PersonalDetails profile={visibleProfile} />
          ) : null}
          <AccountIdentityPanel />
        </div>
        <div className="min-w-0 space-y-5">
          {visibleProfile && <WorkAccess profile={visibleProfile} />}
          <SecurityCallout />
        </div>
      </div>
      <nav aria-label="Личные настройки" className="grid gap-3 sm:grid-cols-3">
        {personalLinks.map((link) => (
          <Link key={link.to} to={link.to} className={`${personalLinkClass} justify-start`}>
            <span className="text-primary">
              <NavIcon to={link.to} />
            </span>
            <span className="min-w-0 flex-1 break-words">{link.label}</span>
            <ArrowIcon />
          </Link>
        ))}
      </nav>
    </div>
  );
}

function ProfileIdentity({ profile }: { profile: PersonalProfile }): JSX.Element {
  const name = profile.full_name.trim() || "Имя не указано";
  const initials = profile.full_name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => Array.from(part)[0])
    .join("")
    .toLocaleUpperCase("ru");
  const context = profile.platform_role
    ? "Личный аккаунт сотрудника Aurum"
    : profile.is_tenant_owner
      ? "Личный аккаунт владельца аптеки"
      : profile.tenant_id
        ? "Личный аккаунт сотрудника аптеки"
        : "Личный аккаунт Aurum Pharma";

  return (
    <section
      aria-label="Сведения об аккаунте"
      className="flex min-w-0 flex-wrap items-center gap-4 rounded-xl border border-border border-l-4 border-l-primary bg-surface p-5 sm:gap-5 sm:p-6"
    >
      <span
        aria-hidden="true"
        className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-xl font-semibold text-primary sm:h-16 sm:w-16"
      >
        {initials || <NavIcon to="/users" />}
      </span>
      <div className="min-w-0 flex-1 basis-40">
        <h2 className="break-words text-xl font-semibold text-foreground sm:text-2xl">{name}</h2>
        <p className="mt-1 text-sm text-foreground-muted">{context}</p>
      </div>
      <Badge tone={profile.status === "active" ? "success" : "warning"}>
        {profile.status === "active" ? "Активен" : "Требует внимания"}
      </Badge>
    </section>
  );
}

function PersonalDetails({ profile }: { profile: PersonalProfile }): JSX.Element {
  return (
    <Card className="rounded-xl">
      <CardHeader className="flex items-center gap-3 border-b-0 pb-0">
        <span className="text-primary" aria-hidden="true">
          <NavIcon to="/users" />
        </span>
        <CardTitle>Личные данные</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="space-y-4">
          <div>
            <dt className="text-sm text-foreground-muted">ФИО</dt>
            <dd className="mt-1 break-words font-medium text-foreground">
              {profile.full_name.trim() || "Имя не указано"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-foreground-muted">Почта для входа</dt>
            <dd className="mt-1 break-all text-foreground-secondary">
              {profile.email.trim() || "Не указана"}
            </dd>
          </div>
        </dl>
        <p className="rounded-lg bg-primary/[0.06] p-3 text-sm leading-6 text-foreground-muted">
          ФИО используется в рабочих документах. Если в нём ошибка, обратитесь к руководителю или
          администратору Aurum.
        </p>
      </CardContent>
    </Card>
  );
}

function WorkAccess({ profile }: { profile: PersonalProfile }): JSX.Element {
  return (
    <Card className="rounded-xl">
      <CardHeader className="flex items-center gap-3 border-b-0 pb-0">
        <span className="text-primary" aria-hidden="true">
          <NavIcon to="/my-work" />
        </span>
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
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <p className="min-w-0 break-words font-medium text-foreground">
                    {assignment.role_name}
                  </p>
                  <Badge>{assignment.scope === "tenant" ? "Все точки" : "Точка"}</Badge>
                </div>
                <p className="mt-2 flex items-start gap-2 break-words text-sm text-foreground-secondary">
                  <NavIcon to="/branches" />
                  <span className="min-w-0">
                    {assignment.scope === "tenant" ? "Все точки аптеки" : assignment.branch?.name}
                  </span>
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
        <p className="rounded-lg bg-primary/[0.06] p-3 text-sm leading-6 text-foreground-muted">
          Доступ определяется назначенными ролями и точками. Изменить его может руководитель или
          уполномоченный администратор.
        </p>
      </CardContent>
    </Card>
  );
}

function SecurityCallout(): JSX.Element {
  return (
    <section
      aria-labelledby="profile-security-heading"
      className="rounded-xl bg-primary p-5 text-primary-foreground sm:p-6"
    >
      <NavIcon to="/security" />
      <h2 id="profile-security-heading" className="mt-3 text-base font-semibold">
        Дополнительная защита
      </h2>
      <p className="mt-2 text-sm leading-6">
        Настройте способы подтверждения важных действий в своём аккаунте.
      </p>
      <Link
        to="/security"
        className="mt-3 inline-flex min-h-11 items-center gap-2 text-sm font-semibold underline decoration-current/40 underline-offset-4 hover:decoration-current focus-visible:outline focus-visible:outline-2 focus-visible:outline-current"
      >
        Открыть безопасность
        <ArrowIcon />
      </Link>
    </section>
  );
}

function ArrowIcon(): JSX.Element {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d="M4 12h16m-6-6 6 6-6 6" />
    </svg>
  );
}
