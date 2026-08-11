import { useState } from "react";

import {
  Badge,
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
import { usePlatformStaffAccounts } from "./queries";
import { type PlatformStaffStatus } from "./types";

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
  const canInvite = hasPlatformCapability(user, PLATFORM_CAPABILITIES.accountsManage);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
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
          canInvite ? (
            <Button onClick={() => setInviteOpen(true)}>Пригласить сотрудника</Button>
          ) : null
        }
      />

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
            </TR>
          </THead>
          <TBody>
            {accounts.data?.items.map((account) => (
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
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      <PlatformInvitationModal open={inviteOpen} onClose={() => setInviteOpen(false)} />
    </div>
  );
}

export default PlatformAccountsPage;
