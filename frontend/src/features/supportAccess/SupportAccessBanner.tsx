import { useCallback, useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";

import { Badge, Button } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { useSupportAccessStore } from "@/stores/supportAccess";

import { clearSupportContext } from "./context";
import { useRevokeSupportSession } from "./queries";

export function SupportAccessBanner(): JSX.Element | null {
  const { user } = useAuth();
  const active = useSupportAccessStore((state) => state.active);
  const navigate = useNavigate();
  const revoke = useRevokeSupportSession();
  const context = user?.support_access;
  const sessionId = active?.id ?? context?.id ?? null;

  const leaveExpiredContext = useCallback(async () => {
    try {
      await clearSupportContext();
    } catch {
      useSupportAccessStore.getState().clear();
    }
    await navigate({ to: "/admin/tenants" });
  }, [navigate]);

  const endAccess = useCallback(async () => {
    if (!sessionId) return;
    const revokeRequest = revoke.mutateAsync(sessionId);
    const identityRefresh = clearSupportContext();
    await Promise.allSettled([revokeRequest, identityRefresh, navigate({ to: "/admin/tenants" })]);
  }, [navigate, revoke, sessionId]);

  useEffect(() => {
    if (!context) return undefined;
    const delay = Math.max(0, new Date(context.expires_at).getTime() - Date.now());
    const timer = window.setTimeout(() => {
      void leaveExpiredContext();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [context, leaveExpiredContext]);

  if (!context || !sessionId) return null;

  return (
    <div className="border-b border-warning/40 bg-warning/10 px-4 py-2 sm:px-6" role="status">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <Badge tone={context.is_read_only ? "neutral" : "warning"}>
          {context.is_read_only ? "просмотр" : "защищённый доступ"}
        </Badge>
        <span className="font-semibold text-foreground">{context.tenant_name}</span>
        <span className="text-foreground-secondary">{context.reason}</span>
        <span className="text-foreground-muted">
          до{" "}
          {new Date(context.expires_at).toLocaleTimeString("ru-RU", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
        <Button
          variant="secondary"
          size="sm"
          className="ml-auto"
          isLoading={revoke.isPending}
          onClick={() => void endAccess()}
        >
          Завершить
        </Button>
      </div>
    </div>
  );
}
