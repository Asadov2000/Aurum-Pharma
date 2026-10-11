import { Fragment, useEffect, type ReactNode } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";

import { useMeQuery } from "./meQueries";
import { isConfirmedAuthFailure } from "./failures";
import { clearClientSession } from "./session";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

export function AuthGuard({ children }: { children: ReactNode }): JSX.Element | null {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const setUser = useAuthStore((s) => s.setUser);
  const navigate = useNavigate();
  const location = useRouterState({ select: (s) => s.location });

  const { data, error, isLoading } = useMeQuery();

  useEffect(() => {
    if (data) setUser(data);
  }, [data, setUser]);

  useEffect(() => {
    if (hydrated && accessToken === null) {
      navigate({ to: "/login", search: { from: location.pathname } });
    }
  }, [hydrated, accessToken, navigate, location.pathname]);

  useEffect(() => {
    if (error && isConfirmedAuthFailure(error)) {
      clearClientSession();
      navigate({ to: "/login", search: { from: location.pathname } });
    }
  }, [error, navigate, location.pathname]);

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
