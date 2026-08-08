import { Outlet, useRouterState } from "@tanstack/react-router";
import { Suspense } from "react";

import { AuthGuard } from "@/features/auth/AuthGuard";

import { AppLayout } from "./AppLayout";
import { RouteAccessGuard } from "./RouteAccessGuard";

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
  if (pathname === "/login") {
    return (
      <Suspense fallback={routePending}>
        <Outlet />
      </Suspense>
    );
  }

  return (
    <AuthGuard>
      <AppLayout>
        <RouteAccessGuard>
          <Suspense fallback={routePending}>
            <Outlet />
          </Suspense>
        </RouteAccessGuard>
      </AppLayout>
    </AuthGuard>
  );
}
