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
import { cn } from "@/lib/utils";

import { EmployeeDirectory } from "./EmployeeDirectory";
import {
  useOffboardUser,
  useRevokeUserSessions,
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

const PAGE_SIZE = 25;
const AssignmentsPanel = lazy(async () => {
  const module = await import("./AssignmentsPanel");
  return { default: module.AssignmentsPanel };
});
const UserProfileForm = lazy(async () => {
  const module = await import("./UserProfileForm");
  return { default: module.UserProfileForm };
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
  const canViewRoles = permissions.includes("roles.view");
  const canViewBranches = permissions.includes("branches.view");
  const showActions = canUpdate || canSuspend || canOffboard || canAssign;

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
        setActionNotice(`Сотрудник «${targetName}» уволен.`);
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
      setActionNotice(
        member.status === "pending"
          ? `Сотрудник «${member.full_name}» активирован.`
          : `Доступ сотрудника «${member.full_name}» возобновлён.`,
      );
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось активировать сотрудника"));
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
            <TableEmpty>К аптеке пока не прикреплены сотрудники</TableEmpty>
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
              showActions={showActions}
              activatingUserId={activateMutation.isPending ? activatingUserId : null}
              registerActionTrigger={(userId, element) => {
                if (element) actionTriggers.current.set(userId, element);
                else actionTriggers.current.delete(userId);
              }}
              onProfile={openProfile}
              onActivate={(member) => void activateMembership(member)}
              onAssignments={openAssignments}
              onRevokeSessions={(member) => ask("sessions", member)}
              onSuspend={(member) => ask("suspend", member)}
              onOffboard={(member) => ask("offboard", member)}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
          </>
        )}
      </section>

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
        title={assignmentUser ? `Роли: ${assignmentUser.full_name}` : ""}
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
        open={pending !== null}
        title={
          pending?.type === "sessions"
            ? "Завершить активные сеансы"
            : pending?.type === "suspend"
              ? "Приостановить доступ"
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
              <>Приостановить доступ для «{pending?.user.full_name}»? Сотрудник не сможет войти.</>
            ) : (
              <>Уволить «{pending?.user.full_name}»? Действие необратимо.</>
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
