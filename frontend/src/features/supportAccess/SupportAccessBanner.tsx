import { useCallback, useEffect, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";

import { Badge, Button } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { useSupportAccessStore } from "@/stores/supportAccess";
import { useAuthStore } from "@/stores/auth";

import { clearSupportContext } from "./context";
import { captureSupportAuthority, isSupportAuthorityCurrent } from "./authority";
import { useRevokeSupportSession } from "./queries";

export function SupportAccessBanner(): JSX.Element | null {
  const { user } = useAuth();
  const active = useSupportAccessStore((state) => state.active);
  const revision = useSupportAccessStore((state) => state.revision);
  const renderedAuthority = useMemo(() => {
    const authority = captureSupportAuthority();
    const matchesRenderedContext =
      useAuthStore.getState().user === user && useSupportAccessStore.getState().active === active;
    return {
      ...authority,
      revision,
      generation: matchesRenderedContext ? authority.generation : -1,
    };
  }, [active, user, revision]);
  const navigate = useNavigate();
  const revoke = useRevokeSupportSession();
  const context = user?.support_access;
  const sessionId = active?.id ?? context?.id ?? null;

  const leaveExpiredContext = useCallback(async () => {
    if (!isSupportAuthorityCurrent(renderedAuthority)) return;
    const refresh = clearSupportContext();
    let authority = captureSupportAuthority();
    try {
      authority = await refresh;
    } catch {
      // clearSupportContext already removed local elevation synchronously.
      if (!isSupportAuthorityCurrent(authority)) return;
    }
    if (isSupportAuthorityCurrent(authority)) await navigate({ to: "/admin/tenants" });
  }, [navigate, renderedAuthority]);

  const endAccess = useCallback(async () => {
    if (!sessionId || !isSupportAuthorityCurrent(renderedAuthority)) return;
    const identityRefresh = clearSupportContext();
    const revokeRequest = revoke.mutateAsync({ sessionId, authority: captureSupportAuthority() });
    await Promise.allSettled([revokeRequest, identityRefresh, navigate({ to: "/admin/tenants" })]);
  }, [navigate, revoke, sessionId, renderedAuthority]);

  useEffect(() => {
    if (
      !context ||
      !isSupportAuthorityCurrent(renderedAuthority) ||
      useAuthStore.getState().user?.support_access?.id !== context.id ||
      (active !== null && active.id !== context.id)
    )
      return undefined;
    const delay = Math.max(0, new Date(context.expires_at).getTime() - Date.now());
    const timer = window.setTimeout(() => {
      void leaveExpiredContext();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [context, leaveExpiredContext, renderedAuthority, active]);

  if (!context || !sessionId || (active !== null && active.id !== context.id)) return null;

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
