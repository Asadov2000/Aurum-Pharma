import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  ConfigurableFilterBar,
  ConfirmDialog,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { createOperationId } from "@/lib/operationId";
import { cn } from "@/lib/utils";

import { EmployeeDirectory } from "./EmployeeDirectory";
import {
  useCreateOwnershipTransfer,
  useOffboardUser,
  useRevokeUserSessions,
  useReissueUserInvitation,
  useRolesQuery,
  useSuspendUser,
  useUpdateUser,
  useUsersQuery,
} from "./queries";
import { isManageableRole } from "./roleAccess";
import { type UserStatus, type UserWithAssignments } from "./types";
import { employeeCountLabel, userStatusLabel } from "./userPresentation";

type Row = UserWithAssignments;
type PendingAction = { type: "sessions" | "suspend" | "offboard"; user: Row };
type OwnershipTransferDraft = { user: Row; operationId: string };

const PAGE_SIZE = 25;
const AssignmentsPanel = lazy(async () => {
  const module = await import("./AssignmentsPanel");
  return { default: module.AssignmentsPanel };
});
const UserProfileForm = lazy(async () => {
  const module = await import("./UserProfileForm");
  return { default: module.UserProfileForm };
});
const InviteEmployeeForm = lazy(async () => {
  const module = await import("./InviteEmployeeForm");
  return { default: module.InviteEmployeeForm };
});

interface UserPanelLoadBoundaryProps {
  children: ReactNode;
  onClose: () => void;
}

interface UserPanelLoadBoundaryState {
  failed: boolean;
}

class UserPanelLoadBoundary extends Component<
  UserPanelLoadBoundaryProps,
  UserPanelLoadBoundaryState
> {
  override state: UserPanelLoadBoundaryState = { failed: false };

  static getDerivedStateFromError(): UserPanelLoadBoundaryState {
    return { failed: true };
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;

    return (
      <div
        role="alert"
        className="grid min-h-48 place-items-center rounded-lg border border-warning/30 bg-warning-subtle p-6 text-center"
      >
        <div className="max-w-sm">
          <h3 className="text-base font-semibold text-foreground">Окно не загрузилось</h3>
          <p className="mt-2 text-sm leading-6 text-foreground-secondary">
            Проверьте подключение и обновите страницу.
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <Button variant="secondary" onClick={this.props.onClose}>
              Закрыть
            </Button>
            <Button onClick={() => window.location.reload()}>Обновить страницу</Button>
          </div>
        </div>
      </div>
    );
  }
}

export function UsersPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("users");
  const tenantId = activeTenantId(user);
  const hasTenant = Boolean(tenantId);
  const isTenantOwner = user?.is_tenant_owner === true;
  const isSupportScoped = user?.support_access !== null && user?.support_access !== undefined;
  const permissions = user?.permissions ?? [];
  const canView = permissions.includes("users.view");
  const canUpdate = (isTenantOwner || isSupportScoped) && permissions.includes("users.update");
  const canSuspend = (isTenantOwner || isSupportScoped) && permissions.includes("users.block");
  const canRevokeSessions = canSuspend;
  const canOffboard = (isTenantOwner || isSupportScoped) && permissions.includes("users.delete");
  const canAssign = (isTenantOwner || isSupportScoped) && permissions.includes("roles.assign");
  const canInvite = isTenantOwner && permissions.includes("users.invite");
  const canViewRoles =
    (isTenantOwner || isSupportScoped) &&
    permissions.some((permission) =>
      ["roles.assign", "roles.create", "roles.update"].includes(permission),
    );
  const canViewBranches = permissions.includes("branches.view");
  const canTransferOwnership = isTenantOwner && !isSupportScoped;
  const canCreateEmployee =
    isTenantOwner && !isSupportScoped && canInvite && canAssign && canViewRoles;
  const showActions =
    canUpdate || canSuspend || canOffboard || canAssign || canInvite || canTransferOwnership;

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<UserStatus | "">("");
  const [roleFilter, setRoleFilter] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [assignmentUserId, setAssignmentUserId] = useState<string | null>(null);
  const [profileUserId, setProfileUserId] = useState<string | null>(null);
  const [profileDirty, setProfileDirty] = useState(false);
  const [profileDiscardOpen, setProfileDiscardOpen] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [ownershipTransfer, setOwnershipTransfer] = useState<OwnershipTransferDraft | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [activatingUserId, setActivatingUserId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const actionTriggers = useRef(new Map<string, HTMLButtonElement>());
  const actionFocusUserId = useRef<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const users = useUsersQuery(
    {
      q,
      status: status || undefined,
      role_id: roleFilter || undefined,
      branch_id: branchFilter || undefined,
      page,
      page_size: PAGE_SIZE,
    },
    hasTenant && canView,
  );
  const roles = useRolesQuery(hasTenant && canViewRoles);
  const branches = useBranchesQuery(true, hasTenant && canViewBranches);
  const suspendMutation = useSuspendUser();
  const offboardMutation = useOffboardUser();
  const revokeSessionsMutation = useRevokeUserSessions();
  const activateMutation = useUpdateUser();
  const reissueMutation = useReissueUserInvitation();
  const ownershipTransferMutation = useCreateOwnershipTransfer();

  const rows = users.data?.items ?? [];
  const total = users.data?.total ?? 0;
  const hasFilters = Boolean(q || status || roleFilter || branchFilter);
  const assignmentUser = assignmentUserId
    ? (rows.find((candidate) => candidate.id === assignmentUserId) ?? null)
    : null;
  const profileUser = profileUserId
    ? (rows.find((candidate) => candidate.id === profileUserId) ?? null)
    : null;
  const catalogueFailures = [
    canViewRoles && roles.error ? "роли" : null,
    canViewBranches && branches.error ? "точки" : null,
  ].filter((value): value is string => value !== null);

  const restoreActionFocus = useCallback(() => {
    const targetId = actionFocusUserId.current;
    actionFocusUserId.current = null;
    if (!targetId) return;
    requestAnimationFrame(() => actionTriggers.current.get(targetId)?.focus());
  }, []);

  const closeProfile = useCallback(() => {
    setProfileDiscardOpen(false);
    setProfileDirty(false);
    setProfileUserId(null);
    restoreActionFocus();
  }, [restoreActionFocus]);

  const requestProfileClose = useCallback(() => {
    if (profileDirty) {
      setProfileDiscardOpen(true);
      return;
    }
    closeProfile();
  }, [closeProfile, profileDirty]);

  const closeAssignments = useCallback(() => {
    setAssignmentUserId(null);
    restoreActionFocus();
  }, [restoreActionFocus]);

  const openProfile = (member: Row) => {
    actionFocusUserId.current = member.id;
    setActionError(null);
    setActionNotice(null);
    setProfileDirty(false);
    setProfileUserId(member.id);
  };

  const openAssignments = (member: Row) => {
    actionFocusUserId.current = member.id;
    setActionError(null);
    setActionNotice(null);
    setAssignmentUserId(member.id);
  };

  const ask = (type: PendingAction["type"], target: Row) => {
    actionFocusUserId.current = target.id;
    setActionError(null);
    setActionNotice(null);
    setPending({ type, user: target });
  };

  const askOwnershipTransfer = (target: Row) => {
    actionFocusUserId.current = target.id;
    setActionError(null);
    setActionNotice(null);
    setOwnershipTransfer({ user: target, operationId: createOperationId() });
  };

  const runOwnershipTransfer = async () => {
    if (!ownershipTransfer) return;
    setActionError(null);
    try {
      await ownershipTransferMutation.mutateAsync({
        operation_id: ownershipTransfer.operationId,
        target_membership_id: ownershipTransfer.user.membership_id,
      });
      setActionNotice(
        `Запрос отправлен сотруднику «${ownershipTransfer.user.full_name}». До подтверждения вы остаётесь владельцем.`,
      );
      setOwnershipTransfer(null);
      restoreActionFocus();
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось создать запрос передачи владения"));
    }
  };

  const runPending = async () => {
    if (!pending) return;
    setActionError(null);
    const targetName = pending.user.full_name;
    try {
      if (pending.type === "sessions") {
        const result = await revokeSessionsMutation.mutateAsync(pending.user.id);
        setActionNotice(
          result.revoked_count > 0
            ? `Завершено активных сеансов: ${result.revoked_count}`
            : "У сотрудника нет активных сеансов.",
        );
      } else if (pending.type === "suspend") {
        await suspendMutation.mutateAsync(pending.user.id);
        setActionNotice(`Доступ сотрудника «${targetName}» приостановлен.`);
      } else {
        await offboardMutation.mutateAsync(pending.user.id);
        setActionNotice(
          pending.user.status === "pending"
            ? `Приглашение для «${targetName}» отозвано.`
            : `Сотрудник «${targetName}» уволен.`,
        );
      }
      setPending(null);
      restoreActionFocus();
    } catch (error) {
      const fallback =
        pending.type === "sessions"
          ? "Не удалось завершить сеансы сотрудника"
          : pending.type === "suspend"
            ? "Не удалось приостановить сотрудника"
            : "Не удалось уволить сотрудника";
      setActionError(describeApiError(error, fallback));
    }
  };

  const activateMembership = async (member: Row) => {
    actionFocusUserId.current = member.id;
    setActionError(null);
    setActionNotice(null);
    setActivatingUserId(member.id);
    try {
      await activateMutation.mutateAsync({
        id: member.id,
        payload: { status: "active" },
      });
      setActionNotice(`Доступ сотрудника «${member.full_name}» возобновлён.`);
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось возобновить доступ сотрудника"));
    } finally {
      setActivatingUserId(null);
      restoreActionFocus();
    }
  };

  const reissueInvitation = async (member: Row) => {
    actionFocusUserId.current = member.id;
    setActionError(null);
    setActionNotice(null);
    setActivatingUserId(member.id);
    try {
      const invitation = await reissueMutation.mutateAsync({
        id: member.id,
        operationId: createOperationId(),
      });
      const deadline = new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Dushanbe",
      }).format(new Date(invitation.invitation_expires_at));
      setActionNotice(
        `Приглашение для «${member.full_name}» обновлено. Войти можно до ${deadline}.`,
      );
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось обновить приглашение"));
    } finally {
      setActivatingUserId(null);
      restoreActionFocus();
    }
  };

  if (!hasTenant || !canView) {
    return (
      <AccessDeniedCard title="Сотрудники" message="У вас нет доступа к сотрудникам этой аптеки." />
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Сотрудники"
        description="Сотрудники аптеки, их статусы и назначенные роли."
        meta={
          users.data && !users.isPlaceholderData
            ? hasFilters
              ? `Найдено: ${total}`
              : `Всего: ${total}`
            : undefined
        }
        actions={
          canCreateEmployee ? (
            <Button
              disabled={roles.isLoading || Boolean(roles.error)}
              onClick={() => {
                setActionError(null);
                setActionNotice(null);
                setInviteOpen(true);
              }}
            >
              + Добавить сотрудника
            </Button>
          ) : undefined
        }
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div>
                <Label htmlFor="user_search">Поиск</Label>
                <Input
                  id="user_search"
                  type="search"
                  autoComplete="off"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="ФИО, email или телефон"
                />
              </div>
            ),
            active: Boolean(qInput.trim()),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="user_status_filter">Статус</Label>
                <Select
                  id="user_status_filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as UserStatus | "");
                    setPage(1);
                  }}
                  className="w-full xl:w-48"
                >
                  <option value="">Все статусы</option>
                  {(Object.keys(userStatusLabel) as UserStatus[]).map((value) => (
                    <option key={value} value={value}>
                      {userStatusLabel[value]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(status),
            onClear: () => {
              setStatus("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "role",
            label: "Роль",
            content: (
              <div>
                <Label htmlFor="user_role_filter">Роль</Label>
                <Select
                  id="user_role_filter"
                  value={roleFilter}
                  disabled={roles.isLoading || roles.isError}
                  onChange={(event) => {
                    setRoleFilter(event.target.value);
                    setPage(1);
                  }}
                  className="w-full xl:w-52"
                >
                  <option value="">Все роли</option>
                  {roles.data
                    ?.filter((role) => role.is_active && isManageableRole(role, tenantId))
                    .map((role) => (
                      <option key={role.id} value={role.id}>
                        {role.name}
                      </option>
                    ))}
                </Select>
              </div>
            ),
            active: Boolean(roleFilter),
            onClear: () => {
              setRoleFilter("");
              setPage(1);
            },
            available: canViewRoles,
          },
          {
            id: "branch",
            label: "Точка",
            content: (
              <div>
                <Label htmlFor="user_branch_filter">Точка</Label>
                <Select
                  id="user_branch_filter"
                  value={branchFilter}
                  disabled={branches.isLoading || branches.isError}
                  onChange={(event) => {
                    setBranchFilter(event.target.value);
                    setPage(1);
                  }}
                  className="w-full xl:w-56"
                >
                  <option value="">Все точки</option>
                  {branches.data?.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchFilter),
            onClear: () => {
              setBranchFilter("");
              setPage(1);
            },
            available: canViewBranches,
          },
        ]}
        onResetValues={() => {
          setQInput("");
          setQ("");
          setStatus("");
          setRoleFilter("");
          setBranchFilter("");
          setPage(1);
        }}
      />

      {catalogueFailures.length > 0 ? (
        <InlineNotice
          tone="warning"
          role="alert"
          action={
            <Button
              variant="secondary"
              size="sm"
              isLoading={roles.isFetching || branches.isFetching}
              onClick={() => {
                if (roles.error) void roles.refetch();
                if (branches.error) void branches.refetch();
              }}
            >
              Повторить
            </Button>
          }
        >
          Не удалось загрузить справочник: {catalogueFailures.join(" и ")}. Основной список
          сотрудников доступен.
        </InlineNotice>
      ) : null}

      {actionError && pending === null ? (
        <InlineNotice tone="danger" role="alert">
          {actionError}
        </InlineNotice>
      ) : null}
      {actionNotice ? (
        <InlineNotice tone="success" role="status">
          {actionNotice}
        </InlineNotice>
      ) : null}

      <section className="space-y-3" aria-labelledby="employee-directory-heading">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 id="employee-directory-heading" className="text-base font-semibold text-foreground">
              Команда аптеки
            </h2>
            <p className="mt-0.5 text-xs text-foreground-muted">
              Контакты, рабочий статус и действующий доступ.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {users.isFetching && !users.isLoading ? (
              <span role="status" className="text-xs text-foreground-muted">
                Обновляем…
              </span>
            ) : null}
            {users.data && !users.isPlaceholderData ? (
              <Badge tone="neutral" aria-live="polite">
                {employeeCountLabel(total)}
              </Badge>
            ) : null}
          </div>
        </div>

        {users.error && (!users.data || users.isPlaceholderData) ? (
          <InlineNotice
            tone="danger"
            role="alert"
            action={
              <Button
                variant="secondary"
                size="sm"
                isLoading={users.isFetching}
                onClick={() => void users.refetch()}
              >
                Повторить
              </Button>
            }
          >
            {describeApiError(users.error, "Не удалось загрузить сотрудников")}
          </InlineNotice>
        ) : users.isLoading ? (
          <SkeletonRows rows={6} />
        ) : rows.length === 0 ? (
          hasFilters ? (
            <TableEmpty title="Ничего не найдено">
              Измените запрос или выбранные фильтры.
            </TableEmpty>
          ) : (
            <TableEmpty title="Сотрудников пока нет">
              Добавьте сотрудника, выберите ему роль и при необходимости ограничьте доступ одной
              торговой точкой.
            </TableEmpty>
          )
        ) : (
          <>
            {users.error ? (
              <InlineNotice
                tone="warning"
                role="alert"
                action={
                  <Button
                    variant="secondary"
                    size="sm"
                    isLoading={users.isFetching}
                    onClick={() => void users.refetch()}
                  >
                    Повторить
                  </Button>
                }
              >
                Показаны последние загруженные данные. Обновить список не удалось.
              </InlineNotice>
            ) : null}
            <EmployeeDirectory
              rows={rows}
              currentUserId={user?.id}
              currentUserIsDeveloper={user?.is_developer === true}
              canUpdate={canUpdate}
              canSuspend={canSuspend}
              canRevokeSessions={canRevokeSessions}
              canOffboard={canOffboard}
              canAssign={canAssign}
              canInvite={canInvite}
              canTransferOwnership={canTransferOwnership}
              showActions={showActions}
              activatingUserId={
                activateMutation.isPending || reissueMutation.isPending ? activatingUserId : null
              }
              registerActionTrigger={(userId, element) => {
                if (element) actionTriggers.current.set(userId, element);
                else actionTriggers.current.delete(userId);
              }}
              onProfile={openProfile}
              onActivate={(member) => void activateMembership(member)}
              onReissueInvitation={(member) => void reissueInvitation(member)}
              onAssignments={openAssignments}
              onTransferOwnership={askOwnershipTransfer}
              onRevokeSessions={(member) => ask("sessions", member)}
              onSuspend={(member) => ask("suspend", member)}
              onOffboard={(member) => ask("offboard", member)}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
          </>
        )}
      </section>

      <Modal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Новый сотрудник"
        className="sm:max-w-2xl"
      >
        {inviteOpen && tenantId ? (
          <UserPanelLoadBoundary onClose={() => setInviteOpen(false)}>
            <Suspense fallback={<SkeletonRows rows={4} />}>
              <InviteEmployeeForm
                tenantId={tenantId}
                roles={roles.data ?? []}
                branches={branches.data ?? []}
                onCreated={(fullName) => {
                  setInviteOpen(false);
                  setActionNotice(`Сотрудник «${fullName}» создан. Приглашение действует 7 дней.`);
                  setPage(1);
                }}
                onCancel={() => setInviteOpen(false)}
              />
            </Suspense>
          </UserPanelLoadBoundary>
        ) : null}
      </Modal>

      <Modal
        open={profileUser !== null}
        onClose={requestProfileClose}
        title={profileUser ? `Профиль: ${profileUser.full_name}` : ""}
        className="sm:max-w-xl"
      >
        {profileUser ? (
          <UserPanelLoadBoundary onClose={closeProfile}>
            <Suspense fallback={<SkeletonRows rows={3} />}>
              <UserProfileForm
                user={profileUser}
                onSaved={closeProfile}
                onCancel={requestProfileClose}
                onDirtyChange={setProfileDirty}
              />
            </Suspense>
          </UserPanelLoadBoundary>
        ) : null}
      </Modal>

      <Modal
        open={assignmentUser !== null}
        onClose={closeAssignments}
        title={assignmentUser ? `Доступ сотрудника: ${assignmentUser.full_name}` : ""}
        className="sm:max-w-2xl"
      >
        {assignmentUser ? (
          <UserPanelLoadBoundary onClose={closeAssignments}>
            <Suspense fallback={<SkeletonRows rows={4} />}>
              <AssignmentsPanel
                user={assignmentUser}
                tenantId={tenantId}
                canManage={canAssign}
                onClose={closeAssignments}
              />
            </Suspense>
          </UserPanelLoadBoundary>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={profileDiscardOpen}
        title="Отменить изменения?"
        message="Изменения профиля сотрудника не сохранятся."
        cancelLabel="Продолжить редактирование"
        confirmLabel="Выйти без сохранения"
        variant="danger"
        onCancel={() => setProfileDiscardOpen(false)}
        onConfirm={closeProfile}
      />

      <ConfirmDialog
        open={ownershipTransfer !== null}
        title="Передать владение аптекой?"
        message={
          <>
            Сотрудник «{ownershipTransfer?.user.full_name}» получит запрос на подтверждение. Его
            текущие сеансы завершатся, а при следующем входе потребуется настроить MFA. До
            подтверждения ваш доступ не изменится.
            {actionError ? (
              <span
                role="alert"
                className="mt-2 block rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-danger-foreground"
              >
                {actionError}
              </span>
            ) : null}
          </>
        }
        confirmLabel="Отправить запрос"
        variant="danger"
        isLoading={ownershipTransferMutation.isPending}
        onConfirm={() => void runOwnershipTransfer()}
        onCancel={() => {
          if (ownershipTransferMutation.isPending) return;
          setOwnershipTransfer(null);
          setActionError(null);
          restoreActionFocus();
        }}
      />

      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.type === "sessions"
            ? "Завершить активные сеансы"
            : pending?.type === "suspend"
              ? "Приостановить доступ"
              : pending?.user.status === "pending"
                ? "Отозвать приглашение"
                : "Уволить сотрудника"
        }
        message={
          <>
            {pending?.type === "sessions" ? (
              <>
                Завершить все сеансы «{pending.user.full_name}»? Сотрудник будет немедленно выведен
                из системы, но сможет войти снова.
              </>
            ) : pending?.type === "suspend" ? (
              <>
                Приостановить доступ для «{pending?.user.full_name}»? Все активные сеансы
                завершатся, и сотрудник не сможет войти до возобновления доступа.
              </>
            ) : pending?.user.status === "pending" ? (
              <>
                Отозвать приглашение для «{pending?.user.full_name}»? Войти по нему больше не
                получится. Действие необратимо.
              </>
            ) : (
              <>
                Уволить «{pending?.user.full_name}»? Все сеансы завершатся, а назначенные роли
                отключатся. Действие необратимо.
              </>
            )}
            {actionError ? (
              <span
                role="alert"
                className="mt-2 block rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-danger-foreground"
              >
                {actionError}
              </span>
            ) : null}
          </>
        }
        confirmLabel={
          pending?.type === "sessions"
            ? "Завершить сеансы"
            : pending?.type === "suspend"
              ? "Приостановить"
              : pending?.user.status === "pending"
                ? "Отозвать"
                : "Уволить"
        }
        variant="danger"
        isLoading={
          revokeSessionsMutation.isPending ||
          suspendMutation.isPending ||
          offboardMutation.isPending
        }
        onConfirm={() => void runPending()}
        onCancel={() => {
          setPending(null);
          setActionError(null);
          restoreActionFocus();
        }}
      />
    </div>
  );
}

function InlineNotice({
  tone,
  role,
  action,
  children,
}: {
  tone: "success" | "warning" | "danger";
  role: "alert" | "status";
  action?: ReactNode;
  children: ReactNode;
}): JSX.Element {
  const toneClass =
    tone === "success"
      ? "border-success/30 bg-success-subtle text-success-foreground"
      : tone === "warning"
        ? "border-warning/30 bg-warning-subtle text-warning-foreground"
        : "border-danger/30 bg-danger-subtle text-danger-foreground";

  return (
    <div
      role={role}
      className={cn(
        "flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm leading-5",
        toneClass,
      )}
    >
      <span className="min-w-0 flex-1">{children}</span>
      {action}
    </div>
  );
}
