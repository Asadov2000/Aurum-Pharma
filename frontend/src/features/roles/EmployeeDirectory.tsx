import { type ReactNode } from "react";

import { ActionMenu, type ActionMenuItem, Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

import { type UserWithAssignments } from "./types";
import {
  employeeInitials,
  formatLastLogin,
  formatInvitationDeadline,
  userStatusLabel,
  userStatusTone,
} from "./userPresentation";

type Row = UserWithAssignments;

interface EmployeeDirectoryProps {
  rows: Row[];
  currentUserId: string | undefined;
  currentUserIsDeveloper: boolean;
  canUpdate: boolean;
  canSuspend: boolean;
  canRevokeSessions: boolean;
  canOffboard: boolean;
  canAssign: boolean;
  canInvite: boolean;
  canTransferOwnership: boolean;
  showActions: boolean;
  activatingUserId: string | null;
  registerActionTrigger: (userId: string, element: HTMLButtonElement | null) => void;
  onProfile: (member: Row) => void;
  onActivate: (member: Row) => void;
  onReissueInvitation: (member: Row) => void;
  onAssignments: (member: Row) => void;
  onTransferOwnership: (member: Row) => void;
  onRevokeSessions: (member: Row) => void;
  onSuspend: (member: Row) => void;
  onOffboard: (member: Row) => void;
}

export function EmployeeDirectory({
  rows,
  currentUserId,
  currentUserIsDeveloper,
  canUpdate,
  canSuspend,
  canRevokeSessions,
  canOffboard,
  canAssign,
  canInvite,
  canTransferOwnership,
  showActions,
  activatingUserId,
  registerActionTrigger,
  onProfile,
  onActivate,
  onReissueInvitation,
  onAssignments,
  onTransferOwnership,
  onRevokeSessions,
  onSuspend,
  onOffboard,
}: EmployeeDirectoryProps): JSX.Element {
  const columns = showActions
    ? "xl:grid-cols-[minmax(12rem,1.2fr)_minmax(12rem,1fr)_minmax(9rem,0.7fr)_minmax(12rem,1.1fr)_minmax(10rem,0.8fr)_3rem]"
    : "xl:grid-cols-[minmax(12rem,1.25fr)_minmax(12rem,1fr)_minmax(9rem,0.7fr)_minmax(12rem,1.1fr)_minmax(10rem,0.8fr)]";

  return (
    <div
      role="table"
      aria-label="Сотрудники аптеки"
      className="w-full min-w-0 max-w-full overflow-hidden rounded-lg border border-border bg-surface"
    >
      <div
        role="row"
        className={cn(
          "hidden gap-3 border-b border-border bg-background/70 px-4 py-3 xl:grid",
          columns,
        )}
      >
        <DirectoryColumnHeader>Сотрудник</DirectoryColumnHeader>
        <DirectoryColumnHeader>Контакты</DirectoryColumnHeader>
        <DirectoryColumnHeader>Статус</DirectoryColumnHeader>
        <DirectoryColumnHeader>Доступ</DirectoryColumnHeader>
        <DirectoryColumnHeader>Последний вход</DirectoryColumnHeader>
        {showActions ? (
          <DirectoryColumnHeader className="text-right">
            <span className="sr-only">Действия</span>
          </DirectoryColumnHeader>
        ) : null}
      </div>
      <ul role="rowgroup" className="min-w-0 max-w-full divide-y divide-border overflow-hidden">
        {rows.map((member) => (
          <EmployeeDirectoryRow
            key={member.id}
            member={member}
            currentUserId={currentUserId}
            currentUserIsDeveloper={currentUserIsDeveloper}
            canUpdate={canUpdate}
            canSuspend={canSuspend}
            canRevokeSessions={canRevokeSessions}
            canOffboard={canOffboard}
            canAssign={canAssign}
            canInvite={canInvite}
            canTransferOwnership={canTransferOwnership}
            showActions={showActions}
            columns={columns}
            activating={activatingUserId === member.id}
            actionRef={(element) => registerActionTrigger(member.id, element)}
            onProfile={() => onProfile(member)}
            onActivate={() => onActivate(member)}
            onReissueInvitation={() => onReissueInvitation(member)}
            onAssignments={() => onAssignments(member)}
            onTransferOwnership={() => onTransferOwnership(member)}
            onRevokeSessions={() => onRevokeSessions(member)}
            onSuspend={() => onSuspend(member)}
            onOffboard={() => onOffboard(member)}
          />
        ))}
      </ul>
    </div>
  );
}

function EmployeeDirectoryRow({
  member,
  currentUserId,
  currentUserIsDeveloper,
  canUpdate,
  canSuspend,
  canRevokeSessions,
  canOffboard,
  canAssign,
  canInvite,
  canTransferOwnership,
  showActions,
  columns,
  activating,
  actionRef,
  onProfile,
  onActivate,
  onReissueInvitation,
  onAssignments,
  onTransferOwnership,
  onRevokeSessions,
  onSuspend,
  onOffboard,
}: {
  member: Row;
  currentUserId: string | undefined;
  currentUserIsDeveloper: boolean;
  canUpdate: boolean;
  canSuspend: boolean;
  canRevokeSessions: boolean;
  canOffboard: boolean;
  canAssign: boolean;
  canInvite: boolean;
  canTransferOwnership: boolean;
  showActions: boolean;
  columns: string;
  activating: boolean;
  actionRef: (element: HTMLButtonElement | null) => void;
  onProfile: () => void;
  onActivate: () => void;
  onReissueInvitation: () => void;
  onAssignments: () => void;
  onTransferOwnership: () => void;
  onRevokeSessions: () => void;
  onSuspend: () => void;
  onOffboard: () => void;
}): JSX.Element {
  const activeAssignments = member.assignments.filter((assignment) => assignment.is_active);
  const shownAssignments = activeAssignments.slice(0, 2);
  const hiddenAssignmentCount = Math.max(0, activeAssignments.length - shownAssignments.length);
  const isOwnerMembership = member.is_tenant_owner;
  const protectsLifecycle = isOwnerMembership || member.id === currentUserId;
  const canEditMember = canUpdate && member.status !== "offboarded";
  const canActivateMember = canUpdate && member.status === "suspended" && !protectsLifecycle;
  const canAssignMember =
    canAssign && (member.status === "pending" || member.status === "active") && !protectsLifecycle;
  const canReissueInvitation =
    canInvite &&
    member.status === "pending" &&
    member.invitation_status === "expired" &&
    !protectsLifecycle;
  const canSuspendMember = canSuspend && !protectsLifecycle && member.status === "active";
  const canRevokeMemberSessions =
    canRevokeSessions &&
    member.id !== currentUserId &&
    member.status === "active" &&
    (!isOwnerMembership || currentUserIsDeveloper);
  const canOffboardMember = canOffboard && !protectsLifecycle && member.status !== "offboarded";
  const canTransferToMember =
    canTransferOwnership && member.status === "active" && !protectsLifecycle;
  const actions: ActionMenuItem[] = [];

  if (canEditMember) actions.push({ label: "Профиль", onSelect: onProfile });
  if (canActivateMember) {
    actions.push({
      label: "Возобновить",
      onSelect: onActivate,
    });
  }
  if (canReissueInvitation) {
    actions.push({ label: "Обновить приглашение", onSelect: onReissueInvitation });
  }
  if (canAssignMember) actions.push({ label: "Роли", onSelect: onAssignments });
  if (canRevokeMemberSessions) {
    actions.push({ label: "Завершить сеансы", onSelect: onRevokeSessions });
  }
  if (canTransferToMember) {
    actions.push({ label: "Передать владение", tone: "danger", onSelect: onTransferOwnership });
  }
  if (canSuspendMember) actions.push({ label: "Приостановить", onSelect: onSuspend });
  if (canOffboardMember) {
    actions.push({
      label: member.status === "pending" ? "Отозвать приглашение" : "Уволить",
      tone: "danger",
      onSelect: onOffboard,
    });
  }

  return (
    <li
      role="row"
      className={cn(
        "relative grid w-full min-w-0 max-w-full grid-cols-1 gap-x-5 gap-y-3 overflow-hidden p-4 transition-colors duration-fast hover:bg-foreground/[0.02] sm:grid-cols-2 xl:items-center xl:gap-3 xl:overflow-visible xl:px-4 xl:py-3",
        columns,
      )}
    >
      <div
        role="cell"
        className={cn(
          "flex min-w-0 items-center gap-3 sm:col-span-2 xl:col-span-1",
          showActions && "pr-12 xl:pr-0",
        )}
      >
        <span
          aria-hidden="true"
          className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold text-primary"
        >
          {employeeInitials(member.full_name)}
        </span>
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span
              className="min-w-0 break-words font-semibold text-foreground"
              title={member.full_name}
            >
              {member.full_name}
            </span>
            {isOwnerMembership ? <Badge tone="info">владелец</Badge> : null}
            {member.id === currentUserId ? <Badge tone="neutral">вы</Badge> : null}
          </div>
        </div>
      </div>

      <DirectoryCell label="Контакты">
        <span className="block break-all text-sm text-foreground-secondary" title={member.email}>
          {member.email}
        </span>
        <span className="mt-0.5 block text-xs text-foreground-muted">
          {member.phone || "Телефон не указан"}
        </span>
      </DirectoryCell>

      <DirectoryCell label="Статус">
        {member.status === "pending" && member.invitation_status === "expired" ? (
          <>
            <Badge tone="warning">Приглашение истекло</Badge>
            <span className="mt-1 block text-xs text-foreground-muted">
              Обновите срок, чтобы сотрудник мог войти
            </span>
          </>
        ) : (
          <>
            <Badge tone={userStatusTone[member.status]}>{userStatusLabel[member.status]}</Badge>
            {member.status === "pending" ? (
              <span className="mt-1 block text-xs text-foreground-muted">
                {formatInvitationDeadline(member.invitation_expires_at)}
              </span>
            ) : null}
          </>
        )}
      </DirectoryCell>

      <DirectoryCell label="Доступ">
        {activeAssignments.length === 0 ? (
          <span className="text-sm text-foreground-muted">Роли не назначены</span>
        ) : (
          <div className="flex min-w-0 flex-wrap gap-1.5">
            {shownAssignments.map((assignment) => {
              const name = assignment.role_name || "Недоступная роль";
              return (
                <Badge key={assignment.id} tone="neutral" className="max-w-full" title={name}>
                  <span className="max-w-48 truncate">{name}</span>
                </Badge>
              );
            })}
            {hiddenAssignmentCount > 0 ? (
              <Badge tone="neutral" title={`Ещё назначений: ${hiddenAssignmentCount}`}>
                +{hiddenAssignmentCount}
              </Badge>
            ) : null}
          </div>
        )}
        {member.status === "pending" ? (
          <span className="mt-1 block text-xs text-foreground-muted">
            Начнёт действовать после первого подтверждённого входа
          </span>
        ) : null}
      </DirectoryCell>

      <DirectoryCell label="Последний вход">
        <span className="text-sm text-foreground-secondary">
          {formatLastLogin(member.last_login_at)}
        </span>
      </DirectoryCell>

      {showActions ? (
        <div
          role="cell"
          className="absolute right-3 top-3 flex justify-end sm:right-4 sm:top-4 xl:static xl:self-center"
        >
          {actions.length > 0 ? (
            <ActionMenu
              ref={actionRef}
              label={`Действия для ${member.full_name}`}
              items={actions}
              isLoading={activating}
            />
          ) : (
            <span className="hidden text-foreground-muted xl:inline">—</span>
          )}
        </div>
      ) : null}
    </li>
  );
}

function DirectoryColumnHeader({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      role="columnheader"
      className={cn("text-xs font-semibold uppercase text-foreground-muted", className)}
    >
      {children}
    </div>
  );
}

function DirectoryCell({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div role="cell" className="min-w-0">
      <span className="mb-1 block text-xs font-medium text-foreground-muted xl:hidden">
        {label}
      </span>
      {children}
    </div>
  );
}
