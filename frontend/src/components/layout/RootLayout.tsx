import { Outlet, useRouterState } from "@tanstack/react-router";
import { lazy, Suspense } from "react";

import { AuthGuard } from "@/features/auth/AuthGuard";

const AuthenticatedShell = lazy(async () => {
  const module = await import("./AuthenticatedShell");
  return { default: module.AuthenticatedShell };
});

const routePending = (
  <div
    role="status"
    aria-live="polite"
    className="flex min-h-48 items-center justify-center text-sm text-foreground-muted"
  >
    Загрузка…
  </div>
);

export function RootLayout(): JSX.Element {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname === "/login" || pathname === "/activate-platform") {
    return (
      <Suspense fallback={routePending}>
        <Outlet />
      </Suspense>
    );
  }

  return (
    <AuthGuard>
      <Suspense fallback={routePending}>
        <AuthenticatedShell>
          <Suspense fallback={routePending}>
            <Outlet />
          </Suspense>
        </AuthenticatedShell>
      </Suspense>
    </AuthGuard>
  );
}
