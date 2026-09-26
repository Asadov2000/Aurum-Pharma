import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { resolveDataPlane } from "@/features/edgeReadModel/dataPlane";
import { useConnectivity } from "@/lib/connectivityContext";
import { useTenantAccessPolicy } from "./queries";
import { type TenantAccessModule } from "./types";

const moduleLabels: Record<TenantAccessModule, string> = {
  catalog: "каталог",
  incoming: "приёмка",
  inventory: "склад",
  suppliers: "поставщики",
  pos: "касса",
  staff: "сотрудники",
  settings: "настройки",
};

export function TenantAccessBanner(): JSX.Element | null {
  const { user, isAuthenticated } = useAuth();
  const tenantId = activeTenantId(user);
  const plane = useMemo(() => resolveDataPlane(), []);
  const online = useConnectivity().canUseServer;
  const canShow = isAuthenticated && user !== null && tenantId !== null && plane.mode === "cloud";
  const { data: policy } = useTenantAccessPolicy(user?.id, tenantId, canShow && online);
  const [, refreshExpiry] = useState(0);
  const expiresAt = policy?.expires_at;
  useEffect(() => {
    if (!expiresAt) return;
    const expires = Date.parse(expiresAt);
    if (!Number.isFinite(expires) || expires <= Date.now()) return;
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      const remaining = expires - Date.now();
      if (remaining <= 0) {
        refreshExpiry((value) => value + 1);
        return;
      }
      timer = setTimeout(schedule, Math.min(remaining, 2_147_483_647));
    };
    schedule();
    return () => clearTimeout(timer);
  }, [expiresAt]);
  if (!canShow || !policy || policy.tenant_id !== tenantId) return null;
  if (policy.expires_at && Date.parse(policy.expires_at) <= Date.now()) return null;
  if (policy.mode === "full" && policy.blocked_modules.length === 0) return null;
  return (
    <div
      role="status"
      className="border-b border-warning/30 bg-warning-subtle px-4 py-2 text-sm text-warning-foreground sm:px-6"
    >
      <span className="font-semibold">Ограничения Aurum.</span>{" "}
      {policy.mode === "readonly"
        ? "Рабочие операции доступны только для чтения."
        : `Изменения ограничены в разделах: ${policy.blocked_modules.map((module) => moduleLabels[module]).join(", ")}.`}{" "}
      Чтение доступно по вашим правам.
      {policy.mode !== "readonly" && policy.blocked_modules.includes("inventory")
        ? " Ограничение склада также затрагивает продажи, приёмку и возвраты, меняющие остатки."
        : null}
      {!online ? " Показано последнее полученное ограничение; обновление после подключения." : null}
    </div>
  );
}
