import { useRouterState } from "@tanstack/react-router";
import { type ReactNode } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { useAuth } from "@/features/auth/hooks";

import { canAccessPath, firstAccessiblePath, getRouteAccessContext } from "./routeAccess";

export function RouteAccessGuard({ children }: { children: ReactNode }): JSX.Element | null {
  const { user } = useAuth();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  if (user === null) return null;

  const context = getRouteAccessContext(user);
  if (canAccessPath(pathname, context)) {
    return <>{children}</>;
  }

  const fallbackTo = firstAccessiblePath(context);
  return (
    <AccessDeniedCard
      title="Раздел недоступен"
      message="Этот раздел не входит в доступы вашего аккаунта. Если он нужен для работы, обратитесь к владельцу аптеки или администратору Aurum Pharma."
      fallbackTo={fallbackTo}
      fallbackLabel="Перейти"
    />
  );
}
