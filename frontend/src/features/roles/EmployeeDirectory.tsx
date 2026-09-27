import { ActionMenu, type ActionMenuItem, Badge, Button } from "@/components/ui";
import { type Branch } from "@/features/foundation/types";

import { EmployeeAssignmentScope, EmployeeStatus } from "./EmployeeDetails";
import { type UserWithAssignments } from "./types";
import { employeeInitials, formatLastLogin } from "./userPresentation";

type Row = UserWithAssignments;

interface EmployeeDirectoryProps {
  rows: Row[];
  branches?: Branch[];
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
  actionsDisabled?: boolean;
  activatingUserId: string | null;
  registerActionTrigger: (userId: string, element: HTMLButtonElement | null) => void;
  onDetails: (member: Row, trigger: HTMLButtonElement) => void;
  onProfile: (member: Row) => void;
  onActivate: (member: Row) => void;
  onReissueInvitation: (member: Row) => void;
  onAssignments: (member: Row, trigger?: HTMLButtonElement) => void;
  onTransferOwnership: (member: Row) => void;
  onRevokeSessions: (member: Row) => void;
  onSuspend: (member: Row) => void;
  onOffboard: (member: Row) => void;
}

export function EmployeeDirectory({
  rows,
  branches = [],
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
  actionsDisabled = false,
  activatingUserId,
  registerActionTrigger,
  onDetails,
  onProfile,
  onActivate,
  onReissueInvitation,
  onAssignments,
  onTransferOwnership,
  onRevokeSessions,
  onSuspend,
  onOffboard,
}: EmployeeDirectoryProps): JSX.Element {
  return (
    <ul
      aria-label="Сотрудники аптеки"
      className="grid w-full min-w-0 grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
    >
      {rows.map((member) => (
        <EmployeeDirectoryRow
          key={member.id}
          member={member}
          branches={branches}
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
          actionsDisabled={actionsDisabled}
          activating={activatingUserId === member.id}
          actionRef={(element) => registerActionTrigger(member.id, element)}
          onDetails={(trigger) => onDetails(member, trigger)}
          onProfile={() => onProfile(member)}
          onActivate={() => onActivate(member)}
          onReissueInvitation={() => onReissueInvitation(member)}
          onAssignments={(trigger) => onAssignments(member, trigger)}
          onTransferOwnership={() => onTransferOwnership(member)}
          onRevokeSessions={() => onRevokeSessions(member)}
          onSuspend={() => onSuspend(member)}
          onOffboard={() => onOffboard(member)}
        />
      ))}
    </ul>
  );
}

function EmployeeDirectoryRow({
  member,
  branches,
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
  actionsDisabled,
  activating,
  actionRef,
  onDetails,
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
  branches: Branch[];
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
  actionsDisabled: boolean;
  activating: boolean;
  actionRef: (element: HTMLButtonElement | null) => void;
  onDetails: (trigger: HTMLButtonElement) => void;
  onProfile: () => void;
  onActivate: () => void;
  onReissueInvitation: () => void;
  onAssignments: (trigger?: HTMLButtonElement) => void;
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
  if (canAssignMember) {
    actions.push({ label: "Настроить доступ", onSelect: () => onAssignments() });
  }
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
    <li className="relative flex min-w-0 flex-col gap-5 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex min-w-0 items-start gap-3 pr-8">
        <span
          aria-hidden="true"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-primary/10 bg-primary/5 text-sm font-semibold text-primary"
        >
          {employeeInitials(member.full_name)}
        </span>
        <div className="min-w-0 space-y-2">
          <h3 className="break-words font-semibold text-foreground" title={member.full_name}>
            {member.full_name}
          </h3>
          {isOwnerMembership || member.id === currentUserId ? (
            <div className="flex flex-wrap gap-1.5">
              {isOwnerMembership ? <Badge tone="info">владелец</Badge> : null}
              {member.id === currentUserId ? <Badge tone="neutral">вы</Badge> : null}
            </div>
          ) : null}
          <EmployeeStatus member={member} />
        </div>
      </div>

      <div className="space-y-2">
        {activeAssignments.length === 0 ? (
          <p className="text-sm text-foreground-muted">Роли не назначены</p>
        ) : (
          <ul aria-label={`Доступ: ${member.full_name}`} className="space-y-2">
            {shownAssignments.map((assignment) => (
              <li key={assignment.id} className="space-y-1 rounded-lg bg-primary/5 p-3">
                <span className="block break-words text-sm font-medium text-foreground">
                  {assignment.role_name || "Недоступная роль"}
                </span>
                <EmployeeAssignmentScope assignment={assignment} branches={branches} />
                {member.status === "suspended" ? (
                  <p className="text-xs text-foreground-muted">Вход приостановлен</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {hiddenAssignmentCount > 0 ? (
          <Badge tone="neutral" title={`Ещё назначений: ${hiddenAssignmentCount}`}>
            +{hiddenAssignmentCount}
          </Badge>
        ) : null}
        {member.status === "pending" ? (
          <p className="text-xs text-foreground-muted">
            Начнёт действовать после первого подтверждённого входа
          </p>
        ) : null}
      </div>

      <dl className="mt-auto space-y-3">
        <div className="min-w-0">
          <dt className="sr-only">Контакты</dt>
          <dd className="break-all text-sm text-foreground-secondary" title={member.email}>
            {member.email}
          </dd>
          <dd className="mt-1 text-xs text-foreground-muted">
            {member.phone || "Телефон не указан"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-foreground-muted">Последний вход</dt>
          <dd className="mt-1 text-xs text-foreground-secondary">
            {formatLastLogin(member.last_login_at)}
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <Button
          variant="secondary"
          size="sm"
          aria-label={`Открыть профиль: ${member.full_name}`}
          onClick={(event) => onDetails(event.currentTarget)}
        >
          Открыть профиль
        </Button>
        {showActions && canAssignMember ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={actionsDisabled}
            onClick={(event) => onAssignments(event.currentTarget)}
          >
            Настроить доступ
          </Button>
        ) : null}
      </div>

      {showActions && actions.length > 0 ? (
        <div className="absolute right-3 top-3">
          <ActionMenu
            ref={actionRef}
            label={`Действия для ${member.full_name}`}
            items={actions}
            isLoading={activating}
            disabled={actionsDisabled}
          />
        </div>
      ) : null}
    </li>
  );
}
