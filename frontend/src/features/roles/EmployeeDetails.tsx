import { Badge } from "@/components/ui";
import { type Branch } from "@/features/foundation/types";

import { type Assignment, type UserWithAssignments } from "./types";
import {
  employeeInitials,
  formatInvitationDeadline,
  formatLastLogin,
  userStatusLabel,
  userStatusTone,
} from "./userPresentation";

export function EmployeeStatus({ member }: { member: UserWithAssignments }): JSX.Element {
  return member.status === "pending" && member.invitation_status === "expired" ? (
    <div>
      <Badge tone="warning">Приглашение истекло</Badge>
      <p className="mt-1 text-xs text-foreground-muted">Обновите срок, чтобы сотрудник мог войти</p>
    </div>
  ) : (
    <div>
      <Badge tone={userStatusTone[member.status]}>{userStatusLabel[member.status]}</Badge>
      {member.status === "pending" ? (
        <p className="mt-1 text-xs text-foreground-muted">
          {formatInvitationDeadline(member.invitation_expires_at)}
        </p>
      ) : null}
    </div>
  );
}

export function EmployeeAssignmentScope({
  assignment,
  branches = [],
  includeFuture = false,
}: {
  assignment: Assignment;
  branches?: Branch[];
  includeFuture?: boolean;
}): JSX.Element {
  const branch = branches.find((item) => item.id === assignment.branch_id);
  const label =
    assignment.branch_id === null
      ? `Все точки аптеки${includeFuture ? ", включая будущие" : ""}`
      : branch
        ? `${branch.name}${branch.is_active ? "" : " · точка отключена"}`
        : "Недоступная точка";

  return <span className="block break-words text-xs text-foreground-muted">{label}</span>;
}

export function EmployeeDetails({
  member,
  currentUserId,
  branches = [],
}: {
  member: UserWithAssignments;
  currentUserId?: string;
  branches?: Branch[];
}): JSX.Element {
  return (
    <div className="space-y-6">
      <div className="flex min-w-0 items-start gap-3">
        <span
          aria-hidden="true"
          className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-primary/10 font-semibold text-primary"
        >
          {employeeInitials(member.full_name)}
        </span>
        <div className="min-w-0 space-y-2">
          <h3 className="break-words text-lg font-semibold text-foreground">{member.full_name}</h3>
          <div className="flex flex-wrap gap-1.5">
            {member.is_tenant_owner ? <Badge tone="info">владелец</Badge> : null}
            {member.id === currentUserId ? <Badge tone="neutral">вы</Badge> : null}
          </div>
          <EmployeeStatus member={member} />
        </div>
      </div>

      <dl className="grid gap-4 rounded-xl border border-border p-4 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-xs text-foreground-muted">Электронная почта</dt>
          <dd className="mt-1 break-all text-sm text-foreground">{member.email}</dd>
        </div>
        <div>
          <dt className="text-xs text-foreground-muted">Телефон</dt>
          <dd className="mt-1 text-sm text-foreground">{member.phone || "Телефон не указан"}</dd>
        </div>
        <div>
          <dt className="text-xs text-foreground-muted">Последний вход · Душанбе (UTC+5)</dt>
          <dd className="mt-1 text-sm text-foreground">{formatLastLogin(member.last_login_at)}</dd>
        </div>
      </dl>

      <section aria-label="Назначения сотрудника" className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Роли и торговые точки</h3>
        {member.assignments.length === 0 ? (
          <p className="text-sm text-foreground-muted">Роли не назначены</p>
        ) : (
          <ul className="space-y-2">
            {member.assignments.map((assignment) => (
              <li key={assignment.id} className="space-y-2 rounded-lg bg-primary/5 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="break-words text-sm font-medium text-foreground">
                    {assignment.role_name || "Недоступная роль"}
                  </span>
                  <Badge tone={assignment.is_active ? "success" : "neutral"}>
                    {assignment.is_active ? "Действующее назначение" : "Неактивное назначение"}
                  </Badge>
                </div>
                <EmployeeAssignmentScope
                  assignment={assignment}
                  branches={branches}
                  includeFuture
                />
                {assignment.is_active && member.status === "suspended" ? (
                  <p className="text-xs text-foreground-muted">Вход приостановлен</p>
                ) : null}
                {assignment.is_active && member.status === "offboarded" ? (
                  <p className="text-xs text-foreground-muted">Сотрудник уволен, доступ закрыт</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {member.status === "pending" ? (
          <p className="text-xs text-foreground-muted">
            Доступ начнёт действовать после первого подтверждённого входа
          </p>
        ) : null}
      </section>
      {member.status === "offboarded" ? (
        <p className="text-xs text-foreground-muted">
          Увольнение сохраняет рабочую историю сотрудника.
        </p>
      ) : null}
    </div>
  );
}
