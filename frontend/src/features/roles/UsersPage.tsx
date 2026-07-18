import { useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  ConfirmDialog,
  Modal,
  Pagination,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/features/foundation/errors";

import { AssignmentsPanel } from "./AssignmentsPanel";
import {
  useOffboardUser,
  useSuspendUser,
  useUpdateUser,
  useUsersQuery,
} from "./queries";
import { type UserStatus, type UserWithAssignments } from "./types";
import { UserProfileForm } from "./UserProfileForm";

type Row = UserWithAssignments;
type PendingAction = { type: "suspend" | "offboard"; user: Row };

const PAGE_SIZE = 50;

const statusTone: Record<UserStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  pending: "info",
  active: "success",
  suspended: "warning",
  offboarded: "danger",
};

const statusLabel: Record<UserStatus, string> = {
  pending: "Ожидает активации",
  active: "Активен",
  suspended: "Приостановлен",
  offboarded: "Уволен",
};

const isSuspended = (status: UserStatus): boolean => status === "suspended";
const isOffboarded = (status: UserStatus): boolean => status === "offboarded";

export function UsersPage(): JSX.Element {
  const { user } = useAuth();
  const hasTenant = Boolean(user?.home_tenant_id);
  const isTenantOwner = user?.is_tenant_owner === true;
  const permissions = user?.permissions ?? [];
  const canView = permissions.includes("users.view");
  const canUpdate = isTenantOwner && permissions.includes("users.update");
  const canSuspend = isTenantOwner && permissions.includes("users.block");
  const canOffboard = isTenantOwner && permissions.includes("users.delete");
  const canAssign = isTenantOwner && permissions.includes("roles.assign");
  const showActions = canUpdate || canSuspend || canOffboard || canAssign;

  const [assignmentUserId, setAssignmentUserId] = useState<string | null>(null);
  const [profileUserId, setProfileUserId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [activatingUserId, setActivatingUserId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const users = useUsersQuery(hasTenant && canView, page, PAGE_SIZE);
  const suspendMutation = useSuspendUser();
  const offboardMutation = useOffboardUser();
  const activateMutation = useUpdateUser();

  const rows = users.data?.items ?? [];
  const assignmentUser = assignmentUserId
    ? (rows.find((candidate) => candidate.id === assignmentUserId) ?? null)
    : null;
  const profileUser = profileUserId
    ? (rows.find((candidate) => candidate.id === profileUserId) ?? null)
    : null;

  const roleName = (assignment: Row["assignments"][number]) =>
    assignment.role_name ?? assignment.role_id.slice(0, 8);

  const ask = (type: PendingAction["type"], target: Row) => {
    setActionError(null);
    setPending({ type, user: target });
  };

  const runPending = async () => {
    if (!pending) return;
    setActionError(null);
    const mutation = pending.type === "suspend" ? suspendMutation : offboardMutation;
    const fallback =
      pending.type === "suspend"
        ? "Не удалось приостановить сотрудника"
        : "Не удалось уволить сотрудника";
    try {
      await mutation.mutateAsync(pending.user.id);
      setPending(null);
    } catch (error) {
      setActionError(describeApiError(error, fallback));
    }
  };

  const activateMembership = async (member: Row) => {
    setActionError(null);
    setActivatingUserId(member.id);
    try {
      await activateMutation.mutateAsync({
        id: member.id,
        payload: { status: "active" },
      });
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось активировать сотрудника"));
    } finally {
      setActivatingUserId(null);
    }
  };

  if (!hasTenant || !canView) {
    return (
      <AccessDeniedCard title="Сотрудники" message="У вас нет доступа к сотрудникам этой аптеки." />
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Сотрудники</h1>

      {actionError && pending === null && <p className="text-sm text-danger">{actionError}</p>}

      {users.error ? (
        <p className="text-sm text-danger">
          {describeApiError(users.error, "Не удалось загрузить сотрудников")}
        </p>
      ) : users.isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        <TableEmpty>К аптеке пока не прикреплены сотрудники</TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Имя</TH>
                <TH>Email</TH>
                <TH>Статус</TH>
                <TH>Роли</TH>
                <TH>Последний вход</TH>
                {showActions && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {rows.map((member) => {
                const activeAssignments = member.assignments.filter(
                  (assignment) => assignment.is_active,
                );
                const isOwnerMembership = member.is_tenant_owner;
                const protectsLifecycle = isOwnerMembership || member.id === user?.id;
                const canEditMember = canUpdate && !isOffboarded(member.status);
                const canActivateMember =
                  canUpdate &&
                  (member.status === "pending" || member.status === "suspended") &&
                  !protectsLifecycle;
                const canAssignMember =
                  canAssign &&
                  member.status === "active" &&
                  !protectsLifecycle;
                const canSuspendMember =
                  canSuspend &&
                  !protectsLifecycle &&
                  !isSuspended(member.status) &&
                  member.status !== "pending" &&
                  !isOffboarded(member.status);
                const canOffboardMember =
                  canOffboard &&
                  !protectsLifecycle &&
                  !isOffboarded(member.status);
                const hasActions =
                  canEditMember ||
                  canActivateMember ||
                  canAssignMember ||
                  canSuspendMember ||
                  canOffboardMember;

                return (
                  <TR key={member.id}>
                    <TD className="font-medium">
                      <span className="inline-flex flex-wrap items-center gap-2">
                        {member.full_name}
                        {isOwnerMembership && <Badge tone="info">владелец</Badge>}
                      </span>
                    </TD>
                    <TD>{member.email}</TD>
                    <TD>
                      <Badge tone={statusTone[member.status]}>{statusLabel[member.status]}</Badge>
                    </TD>
                    <TD>
                      {activeAssignments.length === 0 ? (
                        <span className="text-foreground-muted">—</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {activeAssignments.map((assignment) => (
                            <Badge key={assignment.id} tone="neutral">
                              {roleName(assignment)}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </TD>
                    <TD>
                      {member.last_login_at
                        ? new Date(member.last_login_at).toLocaleString("ru-RU")
                        : "—"}
                    </TD>
                    {showActions && (
                      <TD className="text-right whitespace-nowrap">
                        {canEditMember && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setProfileUserId(member.id)}
                          >
                            Профиль
                          </Button>
                        )}
                        {canActivateMember && (
                          <Button
                            variant="ghost"
                            size="sm"
                            isLoading={activateMutation.isPending && activatingUserId === member.id}
                            onClick={() => void activateMembership(member)}
                          >
                            {member.status === "pending" ? "Активировать" : "Возобновить"}
                          </Button>
                        )}
                        {canAssignMember && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setAssignmentUserId(member.id)}
                          >
                            Роли
                          </Button>
                        )}
                        {canSuspendMember && (
                          <Button variant="ghost" size="sm" onClick={() => ask("suspend", member)}>
                            Приостановить
                          </Button>
                        )}
                        {canOffboardMember && (
                          <Button variant="ghost" size="sm" onClick={() => ask("offboard", member)}>
                            Уволить
                          </Button>
                        )}
                        {!hasActions && <span className="text-foreground-muted">—</span>}
                      </TD>
                    )}
                  </TR>
                );
              })}
            </TBody>
          </Table>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={users.data?.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      <Modal
        open={profileUser !== null}
        onClose={() => setProfileUserId(null)}
        title={profileUser ? `Профиль: ${profileUser.full_name}` : ""}
      >
        {profileUser && (
          <UserProfileForm user={profileUser} onClose={() => setProfileUserId(null)} />
        )}
      </Modal>

      <Modal
        open={assignmentUser !== null}
        onClose={() => setAssignmentUserId(null)}
        title={assignmentUser ? `Роли: ${assignmentUser.full_name}` : ""}
      >
        {assignmentUser && (
          <AssignmentsPanel
            user={assignmentUser}
            tenantId={user?.home_tenant_id ?? null}
            canManage={canAssign}
            onClose={() => setAssignmentUserId(null)}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.type === "suspend" ? "Приостановить доступ" : "Уволить сотрудника"}
        message={
          <>
            {pending?.type === "suspend" ? (
              <>Приостановить доступ для «{pending?.user.full_name}»? Сотрудник не сможет войти.</>
            ) : (
              <>Уволить «{pending?.user.full_name}»? Действие необратимо.</>
            )}
            {actionError && <span className="mt-2 block text-danger">{actionError}</span>}
          </>
        }
        confirmLabel={pending?.type === "suspend" ? "Приостановить" : "Уволить"}
        variant="danger"
        isLoading={suspendMutation.isPending || offboardMutation.isPending}
        onConfirm={() => void runPending()}
        onCancel={() => {
          setPending(null);
          setActionError(null);
        }}
      />
    </div>
  );
}
