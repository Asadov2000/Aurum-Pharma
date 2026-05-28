import { useState } from "react";

import {
  Badge,
  Button,
  Modal,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
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
  const [inviting, setInviting] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const users = useUsersQuery();
  const roles = useRolesQuery();
  const blockMutation = useBlockUser();
  const archiveMutation = useArchiveUser();

  // Always derive the editing target from the latest query data so the
  // AssignmentsPanel re-renders with fresh assignments after mutations.
  const editing = editingId
    ? users.data?.find((u) => u.id === editingId) ?? null
    : null;

  const roleName = (id: string) => roles.data?.find((r) => r.id === id)?.name ?? id.slice(0, 8);

  const onBlock = async (u: Row) => {
    if (!window.confirm(`Заблокировать пользователя «${u.full_name}»?`)) return;
    try {
      await blockMutation.mutateAsync(u.id);
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось заблокировать"));
    }
  };

  const onArchive = async (u: Row) => {
    if (!window.confirm(`Архивировать пользователя «${u.full_name}»? Действие необратимо.`)) return;
    try {
      await archiveMutation.mutateAsync(u.id);
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось архивировать"));
    }
  };

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
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !users.data || users.data.length === 0 ? (
        <TableEmpty>Пока нет пользователей</TableEmpty>
      ) : (
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
            {users.data.map((u) => {
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
                      <Button variant="ghost" size="sm" onClick={() => void onBlock(u)}>
                        Блок
                      </Button>
                    )}
                    {u.status !== "archived" && (
                      <Button variant="ghost" size="sm" onClick={() => void onArchive(u)}>
                        Архив
                      </Button>
                    )}
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
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
    </div>
  );
}
