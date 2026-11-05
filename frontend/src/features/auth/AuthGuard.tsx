import { Fragment, useEffect, type ReactNode } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";

import { useMeQuery } from "./meQueries";
import { isConfirmedAuthFailure } from "./failures";
import { clearClientSession } from "./session";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

export function AuthGuard({ children }: { children: ReactNode }): JSX.Element | null {
  const { accessToken, user, hydrated, setUser } = useAuthStore();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  const { data, error, isLoading } = useMeQuery();

  useEffect(() => {
    if (data) setUser(data);
  }, [data, setUser]);

  useEffect(() => {
    if (isConfirmedAuthFailure(error)) {
      clearClientSession();
    } else if (!hydrated || accessToken !== null) {
      return;
    }
    navigate({ to: "/login", search: { from: pathname } });
  }, [error, hydrated, accessToken, navigate, pathname]);

  if (!hydrated || accessToken === null) return null;
  if (isLoading && user === null) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-foreground-muted">
        Загрузка…
      </div>
    );
  }
  if (user === null) return null;
  // Query observers and page-local data must not survive a change of authority.
  return <Fragment key={getAuthSessionGeneration()}>{children}</Fragment>;
}
