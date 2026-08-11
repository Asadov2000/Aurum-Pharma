import { useState } from "react";

import {
  Badge,
  ActionMenu,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  PageHeader,
  Select,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPlatformCapability, PLATFORM_CAPABILITIES } from "@/features/auth/platformCapabilities";
import { describeApiError } from "@/lib/errorMessages";

import { PlatformInvitationModal } from "./PlatformInvitationModal";
import { PlatformAccountActionModal } from "./PlatformAccountActionModal";
import { usePlatformStaffAccounts } from "./queries";
import {
  type PlatformAccountAction,
  type PlatformStaffAccount,
  type PlatformStaffStatus,
} from "./types";

type StatusFilter = "all" | PlatformStaffStatus;

const statusLabel: Record<PlatformStaffStatus, string> = {
  invited: "Ожидает активации",
  active: "Активен",
  blocked: "Заблокирован",
  offboarded: "Выведен из команды",
};

const statusTone: Record<PlatformStaffStatus, "info" | "success" | "danger" | "neutral"> = {
  invited: "info",
  active: "success",
  blocked: "danger",
  offboarded: "neutral",
};

export function PlatformAccountsPage(): JSX.Element {
  const { user } = useAuth();
  const preferenceKey = useFilterPreferenceKey("platform-accounts");
  const canManage = hasPlatformCapability(user, PLATFORM_CAPABILITIES.accountsManage);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<PlatformStaffAccount | null>(null);
  const [selectedAction, setSelectedAction] = useState<PlatformAccountAction>("reinvite");
  const [notice, setNotice] = useState<string | null>(null);
  const accounts = usePlatformStaffAccounts({
    q: search.trim().length >= 2 ? search.trim() : undefined,
    status: status === "all" ? undefined : status,
    limit: 100,
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Команда Aurum"
        description="Аккаунты платформы без смешивания с сотрудниками аптек"
        meta={<>найдено: {accounts.data?.total ?? 0}</>}
        actions={
          canManage ? (
            <Button onClick={() => setInviteOpen(true)}>Пригласить сотрудника</Button>
          ) : null
        }
      />

      {notice && (
        <div role="status" className="rounded-md border border-info/30 bg-info-subtle p-3 text-sm">
          {notice}
        </div>
      )}

      <ConfigurableFilterBar
        preferenceKey={preferenceKey}
        onResetValues={() => {
          setStatus("all");
          setSearch("");
        }}
        filters={[
          {
            id: "status",
            label: "Статус",
            alwaysVisible: true,
            active: status !== "all",
            onClear: () => setStatus("all"),
            content: (
              <div>
                <Label htmlFor="platform-account-status">Статус</Label>
                <Select
                  id="platform-account-status"
                  value={status}
                  onChange={(event) => setStatus(event.target.value as StatusFilter)}
                  className="w-full sm:w-52"
                >
                  <option value="all">Все статусы</option>
                  <option value="invited">Ожидает активации</option>
                  <option value="active">Активен</option>
                  <option value="blocked">Заблокирован</option>
                  <option value="offboarded">Выведен из команды</option>
                </Select>
              </div>
            ),
          },
          {
            id: "search",
            label: "Сотрудник",
            defaultVisible: true,
            active: Boolean(search),
            onClear: () => setSearch(""),
            content: (
              <div>
                <Label htmlFor="platform-account-search">Сотрудник</Label>
                <Input
                  id="platform-account-search"
                  type="search"
                  placeholder="Имя или email"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="w-full sm:w-64"
                />
              </div>
            ),
          },
        ]}
      />

      {accounts.isLoading && <SkeletonRows rows={6} />}
      {accounts.error && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-subtle p-4">
          <p className="text-sm">
            {describeApiError(accounts.error, "Не удалось загрузить команду Aurum.")}
          </p>
          <Button className="mt-3" size="sm" variant="secondary" onClick={() => accounts.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      {!accounts.isLoading && !accounts.error && accounts.data?.items.length === 0 && (
        <TableEmpty title="Аккаунты не найдены">
          Измените фильтры или создайте первое приглашение.
        </TableEmpty>
      )}

      {!accounts.isLoading && !accounts.error && (accounts.data?.items.length ?? 0) > 0 && (
        <Table aria-label="Команда Aurum">
          <THead>
            <TR>
              <TH>Сотрудник</TH>
              <TH>Статус аккаунта</TH>
              <TH>Приглашён</TH>
              <TH>Активация</TH>
              {canManage && <TH className="text-right">Действия</TH>}
            </TR>
          </THead>
          <TBody>
            {accounts.data?.items.map((account) => {
              const actions = account.user_id === user?.id ? [] : actionsForStatus(account.status);
              return (
                <TR key={account.user_id}>
                  <TD>
                    <div className="max-w-72">
                      <p className="truncate font-medium">{account.full_name}</p>
                      <p className="truncate text-xs text-foreground-muted">{account.email}</p>
                    </div>
                  </TD>
                  <TD>
                    <Badge tone={statusTone[account.status]}>{statusLabel[account.status]}</Badge>
                  </TD>
                  <TD className="whitespace-nowrap">
                    {new Date(account.invited_at).toLocaleString("ru-RU")}
                  </TD>
                  <TD className="whitespace-nowrap">
                    {account.activated_at
                      ? new Date(account.activated_at).toLocaleString("ru-RU")
                      : account.invitation_expires_at
                        ? `до ${new Date(account.invitation_expires_at).toLocaleString("ru-RU")}`
                        : "—"}
                  </TD>
                  {canManage && (
                    <TD className="text-right">
                      {actions.length > 0 ? (
                        <ActionMenu
                          label={`Действия с аккаунтом ${account.full_name}`}
                          items={actions.map((action) => ({
                            label: actionLabel[action],
                            tone:
                              action === "block" || action === "offboard" ? "danger" : "default",
                            onSelect: () => {
                              setSelectedAction(action);
                              setSelectedAccount(account);
                            },
                          }))}
                        />
                      ) : (
                        <span className="text-foreground-muted">—</span>
                      )}
                    </TD>
                  )}
                </TR>
              );
            })}
          </TBody>
        </Table>
      )}

      <PlatformInvitationModal open={inviteOpen} onClose={() => setInviteOpen(false)} />
      <PlatformAccountActionModal
        action={selectedAction}
        account={selectedAccount}
        open={selectedAccount !== null}
        onClose={() => setSelectedAccount(null)}
        onCompleted={(action) => {
          setNotice(actionSuccessMessage[action]);
        }}
        onRefreshRequired={(message) => {
          setNotice(message);
          void accounts.refetch();
        }}
      />
    </div>
  );
}

const actionLabel: Record<PlatformAccountAction, string> = {
  reinvite: "Отправить приглашение повторно",
  block: "Заблокировать",
  unblock: "Разблокировать",
  offboard: "Вывести из команды",
};

const actionSuccessMessage: Record<PlatformAccountAction, string> = {
  reinvite: "Новое приглашение создано.",
  block: "Аккаунт заблокирован, сессии и права отозваны.",
  unblock: "Аккаунт разблокирован. Права не восстановлены.",
  offboard: "Сотрудник выведен из команды.",
};

function actionsForStatus(status: PlatformStaffStatus): PlatformAccountAction[] {
  if (status === "invited") return ["reinvite", "offboard"];
  if (status === "active") return ["block", "offboard"];
  if (status === "blocked") return ["unblock", "offboard"];
  return [];
}

export default PlatformAccountsPage;
