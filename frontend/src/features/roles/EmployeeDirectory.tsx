import { useRef } from "react";

import { Badge, Button } from "@/components/ui";
import { RecordActionMenu, type RecordAction } from "@/features/foundation/RecordActionMenu";
import { type Branch } from "@/features/foundation/types";
import { type QuickActionMenuPreferences } from "@/features/settings/quickActions";

import { EmployeeAssignmentScope, EmployeeStatus } from "./EmployeeDetails";
import { type UserWithAssignments } from "./types";
import { employeeInitials, formatLastLogin, userStatusLabel } from "./userPresentation";

type Row = UserWithAssignments;

interface EmployeeDirectoryProps {
  quickActionConfig: QuickActionMenuPreferences;
  quickActionScope: string;
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
  quickActionConfig,
  quickActionScope,
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
          quickActionConfig={quickActionConfig}
          quickActionScope={quickActionScope}
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
  quickActionConfig,
  quickActionScope,
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
  quickActionConfig: QuickActionMenuPreferences;
  quickActionScope: string;
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
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
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
  const actions: RecordAction[] = [
    {
      id: "open",
      label: "Открыть карточку",
      onSelect: () => {
        if (menuTrigger.current) onDetails(menuTrigger.current);
      },
    },
  ];

  if (showActions && canEditMember)
    actions.push({ id: "profile", label: "Профиль", onSelect: onProfile });
  if (showActions && canActivateMember) {
    actions.push({
      id: "activate",
      label: "Возобновить",
      onSelect: onActivate,
    });
  }
  if (showActions && canReissueInvitation) {
    actions.push({
      id: "reissue_invitation",
      label: "Обновить приглашение",
      onSelect: onReissueInvitation,
    });
  }
  if (showActions && canAssignMember) {
    actions.push({
      id: "assign_access",
      label: "Настроить доступ",
      onSelect: () => onAssignments(),
    });
  }
  if (showActions && canRevokeMemberSessions) {
    actions.push({
      id: "revoke_sessions",
      label: "Завершить сеансы",
      tone: "danger",
      onSelect: onRevokeSessions,
    });
  }
  if (showActions && canTransferToMember) {
    actions.push({
      id: "transfer_ownership",
      label: "Передать владение",
      tone: "danger",
      onSelect: onTransferOwnership,
    });
  }
  if (showActions && canSuspendMember)
    actions.push({ id: "suspend", label: "Приостановить", tone: "danger", onSelect: onSuspend });
  if (showActions && canOffboardMember) {
    actions.push({
      id: "offboard",
      label: member.status === "pending" ? "Отозвать приглашение" : "Уволить",
      tone: "danger",
      onSelect: onOffboard,
    });
  }

  return (
    <li
      data-context-actions
      tabIndex={0}
      className="relative flex min-w-0 flex-col gap-5 rounded-xl border border-border bg-surface p-5 shadow-sm"
    >
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

      <div className="absolute right-3 top-3">
        <RecordActionMenu
          triggerRef={(element) => {
            menuTrigger.current = element;
            actionRef(element);
          }}
          section="employees"
          recordKey={JSON.stringify([
            member.id,
            member.membership_id,
            member.status,
            member.invitation_status,
            member.invitation_expires_at,
            member.is_tenant_owner,
            member.assignments.map((assignment) => [
              assignment.id,
              assignment.role_version_id,
              assignment.is_active,
            ]),
          ])}
          title={member.full_name}
          detail={userStatusLabel[member.status]}
          label={`Действия для ${member.full_name}`}
          items={actions}
          config={quickActionConfig}
          scope={quickActionScope}
          isLoading={activating}
          disabled={actionsDisabled}
        />
      </div>
    </li>
  );
}
