import { useState } from "react";

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
import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/features/foundation/errors";

import { AssignmentsPanel } from "./AssignmentsPanel";
import { InviteUserModal } from "./InviteUserModal";
import {
  useArchiveUser,
  useBlockUser,
  useRolesQuery,
  useUsersQuery,
} from "./queries";
import { type UserStatus, type UserWithAssignments } from "./types";

// UserWithAssignments is used implicitly via the query data + onBlock/onArchive args.
type Row = UserWithAssignments;

const PAGE_SIZE = 50;

const statusTone: Record<UserStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  invited: "info",
  active: "success",
  blocked: "warning",
  archived: "danger",
};

const statusLabel: Record<UserStatus, string> = {
  invited: "Приглашён",
  active: "Активен",
  blocked: "Заблокирован",
  archived: "Архив",
};

export function UsersPage(): JSX.Element {
  const { user } = useAuth();
  const hasTenant = Boolean(user?.home_tenant_id);
  // Team management is gated by users.view on the backend (owner/admin/dev).
  const canManage =
    Boolean(user?.is_developer || user?.is_administrator) ||
    (user?.permissions ?? []).includes("users.view");

  const [inviting, setInviting] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pending, setPending] = useState<{ type: "block" | "archive"; user: Row } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const users = useUsersQuery(hasTenant && canManage, page, PAGE_SIZE);
  const roles = useRolesQuery(hasTenant && canManage);
  const blockMutation = useBlockUser();
  const archiveMutation = useArchiveUser();

  const rows = users.data?.items ?? [];

  // Always derive the editing target from the latest query data so the
  // AssignmentsPanel re-renders with fresh assignments after mutations.
  const editing = editingId ? rows.find((u) => u.id === editingId) ?? null : null;

  const roleName = (id: string) => roles.data?.find((r) => r.id === id)?.name ?? id.slice(0, 8);

  const ask = (type: "block" | "archive", u: Row) => {
    setActionError(null);
    setPending({ type, user: u });
  };

  const runPending = async () => {
    if (!pending) return;
    setActionError(null);
    const mutation = pending.type === "block" ? blockMutation : archiveMutation;
    const fail = pending.type === "block" ? "Не удалось заблокировать" : "Не удалось архивировать";
    try {
      await mutation.mutateAsync(pending.user.id);
      setPending(null);
    } catch (err) {
      setActionError(describeApiError(err, fail));
    }
  };

  // A tenant user without users.view (e.g. a seller) gets a friendly note
  // instead of a screen that only 403s. Support users (no tenant) fall through.
  if (hasTenant && !canManage) {
    return (
      <AccessDeniedCard
        title="Пользователи"
        message="Управление сотрудниками доступно владельцу и администратору."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Пользователи</h1>
        <Button onClick={() => setInviting(true)}>+ Пригласить</Button>
      </div>
      {users.error && (
        <p className="text-sm text-danger">
          {describeApiError(users.error, "Не удалось загрузить пользователей")}
        </p>
      )}
      {users.isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        <TableEmpty>Пока нет пользователей</TableEmpty>
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
              <TH className="text-right">Действия</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((u) => {
              const activeAssignments = u.assignments.filter((a) => a.is_active);
              return (
                <TR key={u.id}>
                  <TD className="font-medium">{u.full_name}</TD>
                  <TD>{u.email}</TD>
                  <TD>
                    <Badge tone={statusTone[u.status]}>{statusLabel[u.status]}</Badge>
                  </TD>
                  <TD>
                    {activeAssignments.length === 0 ? (
                      <span className="text-foreground-muted">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {activeAssignments.map((a) => (
                          <Badge key={a.id} tone="neutral">
                            {roleName(a.role_id)}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </TD>
                  <TD>
                    {u.last_login_at
                      ? new Date(u.last_login_at).toLocaleString("ru-RU")
                      : "—"}
                  </TD>
                  <TD className="text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" onClick={() => setEditingId(u.id)}>
                      Роли
                    </Button>
                    {u.status !== "blocked" && u.status !== "archived" && (
                      <Button variant="ghost" size="sm" onClick={() => ask("block", u)}>
                        Блок
                      </Button>
                    )}
                    {u.status !== "archived" && (
                      <Button variant="ghost" size="sm" onClick={() => ask("archive", u)}>
                        Архив
                      </Button>
                    )}
                  </TD>
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

      <Modal open={inviting} onClose={() => setInviting(false)} title="Пригласить пользователя">
        <InviteUserModal onClose={() => setInviting(false)} />
      </Modal>

      <Modal
        open={editing !== null}
        onClose={() => setEditingId(null)}
        title={editing ? `Роли: ${editing.full_name}` : ""}
      >
        {editing && (
          <AssignmentsPanel user={editing} onClose={() => setEditingId(null)} />
        )}
      </Modal>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.type === "block" ? "Заблокировать сотрудника" : "Архивировать сотрудника"}
        message={
          <>
            {pending?.type === "block" ? (
              <>
                Заблокировать «{pending?.user.full_name}»? Он не сможет войти, пока вы не снимете
                блокировку.
              </>
            ) : (
              <>Архивировать «{pending?.user.full_name}»? Действие необратимо.</>
            )}
            {actionError && <span className="mt-2 block text-danger">{actionError}</span>}
          </>
        }
        confirmLabel={pending?.type === "block" ? "Заблокировать" : "Архивировать"}
        variant="danger"
        isLoading={blockMutation.isPending || archiveMutation.isPending}
        onConfirm={() => void runPending()}
        onCancel={() => {
          setPending(null);
          setActionError(null);
        }}
      />
    </div>
  );
}
