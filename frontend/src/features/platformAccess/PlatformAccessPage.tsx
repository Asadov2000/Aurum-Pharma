import { useMemo, useState } from "react";

import {
  ActionMenu,
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

import {
  accessKindLabel,
  accessStatusLabel,
  accessStatusTone,
  platformCapabilityLabel,
} from "./labels";
import { PlatformGrantActionModal, type PlatformGrantAction } from "./PlatformGrantActionModal";
import { usePlatformAccessGrants } from "./queries";
import { type PlatformAccessGrant, type PlatformAccessStatus } from "./types";

type StatusFilter = "all" | PlatformAccessStatus;

export function PlatformAccessPage(): JSX.Element {
  const { user } = useAuth();
  const preferenceKey = useFilterPreferenceKey("platform-access");
  const canManage = hasPlatformCapability(user, PLATFORM_CAPABILITIES.accessManage);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [selectedGrant, setSelectedGrant] = useState<PlatformAccessGrant | null>(null);
  const [action, setAction] = useState<PlatformGrantAction>("approve");
  const [notice, setNotice] = useState<string | null>(null);
  const grants = usePlatformAccessGrants({
    status: status === "all" ? undefined : status,
    limit: 100,
  });

  const visibleGrants = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    if (!needle) return grants.data ?? [];
    return (grants.data ?? []).filter((grant) =>
      [grant.user_full_name, grant.user_email, grant.user_id]
        .filter((value): value is string => Boolean(value))
        .some((value) => value.toLocaleLowerCase("ru-RU").includes(needle)),
    );
  }, [grants.data, search]);

  const openAction = (grant: PlatformAccessGrant, nextAction: PlatformGrantAction) => {
    setNotice(null);
    setSelectedGrant(grant);
    setAction(nextAction);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Доступ платформы"
        description="Заявки и активные назначения"
        meta={<>найдено: {visibleGrants.length}</>}
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
                <Label htmlFor="platform-access-status">Статус</Label>
                <Select
                  id="platform-access-status"
                  value={status}
                  onChange={(event) => setStatus(event.target.value as StatusFilter)}
                  className="w-full sm:w-52"
                >
                  <option value="all">Все статусы</option>
                  <option value="pending">Ожидает подтверждения</option>
                  <option value="active">Активен</option>
                  <option value="revoked">Отозван</option>
                  <option value="expired">Истёк</option>
                </Select>
              </div>
            ),
          },
          {
            id: "account",
            label: "Аккаунт",
            defaultVisible: true,
            active: Boolean(search),
            onClear: () => setSearch(""),
            content: (
              <div>
                <Label htmlFor="platform-access-search">Аккаунт</Label>
                <Input
                  id="platform-access-search"
                  type="search"
                  placeholder="Имя, email или ID"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="w-full sm:w-64"
                />
              </div>
            ),
          },
        ]}
      />

      {notice && (
        <p role="status" className="rounded-md border border-border bg-surface px-3 py-2 text-sm">
          {notice}
        </p>
      )}

      {grants.isLoading && <SkeletonRows rows={6} />}

      {grants.error && (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger-subtle p-4">
          <p className="text-sm text-danger">
            {describeApiError(grants.error, "Не удалось загрузить доступы платформы")}
          </p>
          <Button className="mt-3" size="sm" variant="secondary" onClick={() => grants.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      {!grants.isLoading && !grants.error && visibleGrants.length === 0 && (
        <TableEmpty title="Доступы не найдены">
          Измените фильтры или проверьте другой аккаунт.
        </TableEmpty>
      )}

      {!grants.isLoading && !grants.error && visibleGrants.length > 0 && (
        <Table aria-label="Доступы платформы">
          <THead>
            <TR>
              <TH>Аккаунт</TH>
              <TH>Тип доступа</TH>
              <TH>Возможности</TH>
              <TH>Статус</TH>
              <TH>Запрошен</TH>
              {canManage && <TH className="w-14 text-right">Действия</TH>}
            </TR>
          </THead>
          <TBody>
            {visibleGrants.map((grant) => {
              const canApprove =
                canManage &&
                grant.status === "pending" &&
                grant.requested_by !== user?.id &&
                grant.user_id !== user?.id;
              const canRevoke =
                canManage &&
                (grant.status === "pending" || grant.status === "active") &&
                grant.user_id !== user?.id;
              const actions = [
                ...(canApprove
                  ? [{ label: "Подтвердить", onSelect: () => openAction(grant, "approve") }]
                  : []),
                ...(canRevoke
                  ? [
                      {
                        label: "Отозвать",
                        tone: "danger" as const,
                        onSelect: () => openAction(grant, "revoke"),
                      },
                    ]
                  : []),
              ];

              return (
                <TR key={grant.id}>
                  <TD>
                    <div className="max-w-64">
                      <p className="truncate font-medium">{grant.user_full_name ?? "Без имени"}</p>
                      <p className="truncate text-xs text-foreground-muted">
                        {grant.user_email ?? grant.user_id}
                      </p>
                    </div>
                  </TD>
                  <TD>{accessKindLabel[grant.access_kind]}</TD>
                  <TD>
                    <CapabilitySummary capabilities={grant.capabilities} />
                  </TD>
                  <TD>
                    <div className="space-y-1">
                      <Badge tone={accessStatusTone[grant.status]}>
                        {accessStatusLabel[grant.status]}
                      </Badge>
                      {grant.status === "pending" && grant.requested_by === user?.id && (
                        <p className="text-xs text-foreground-muted">Нужен другой разработчик</p>
                      )}
                    </div>
                  </TD>
                  <TD className="whitespace-nowrap">
                    {new Date(grant.requested_at).toLocaleString("ru-RU")}
                  </TD>
                  {canManage && (
                    <TD className="text-right">
                      {actions.length > 0 ? (
                        <ActionMenu label="Действия с доступом" items={actions} />
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

      <PlatformGrantActionModal
        action={action}
        grant={selectedGrant}
        open={selectedGrant !== null}
        onClose={() => setSelectedGrant(null)}
        onCompleted={(updated) => {
          setNotice(
            updated.status === "expired"
              ? "Срок подтверждения истёк. Заявка закрыта без выдачи доступа."
              : action === "approve"
                ? "Доступ подтверждён."
                : "Доступ отозван.",
          );
        }}
        onRefreshRequired={setNotice}
      />
    </div>
  );
}

export default PlatformAccessPage;

function CapabilitySummary({ capabilities }: { capabilities: string[] }): JSX.Element {
  const visible = capabilities.slice(0, 2);
  return (
    <div className="flex max-w-72 flex-wrap gap-1" title={capabilities.join(", ")}>
      {visible.map((capability) => (
        <Badge key={capability} tone="neutral">
          {platformCapabilityLabel[capability] ?? capability}
        </Badge>
      ))}
      {capabilities.length > visible.length && (
        <Badge tone="info">+{capabilities.length - visible.length}</Badge>
      )}
    </div>
  );
}
