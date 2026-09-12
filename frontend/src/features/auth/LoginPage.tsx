import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import {
  accessibleInternalPath,
  firstAccessiblePath,
  getRouteAccessContext,
} from "@/components/layout/routeAccess";
import { describeApiError } from "@/lib/errorMessages";
import { resolvePublicWebsiteUrl } from "@/lib/publicWebsite";
import { useAuthStore } from "@/stores/auth";

import { fetchMe } from "./api";
import { LoginLayout } from "./LoginLayout";
import { LoginCredentialsForm } from "./LoginCredentialsForm";
import { clearClientSession } from "./session";
import { type LoginVerifyResponse, type MfaChallengeResponse, type TokenPair } from "./types";

const MfaLoginFlow = lazy(async () => {
  const module = await import("./MfaLoginFlow");
  return { default: module.MfaLoginFlow };
});

export function LoginPage(): JSX.Element {
  const publicWebsiteUrl = resolvePublicWebsiteUrl(import.meta.env.VITE_PUBLIC_WEBSITE_URL);
  const [challenge, setChallenge] = useState<MfaChallengeResponse | null>(null);
  const [enrollmentVisible, setEnrollmentVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequestRef = useRef<AbortController | null>(null);
  const flowVersionRef = useRef(0);
  const setTokens = useAuthStore((state) => state.setTokens);
  const setUser = useAuthStore((state) => state.setUser);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { from?: string };

  useEffect(
    () => () => {
      flowVersionRef.current += 1;
      activeRequestRef.current?.abort();
    },
    [],
  );

  const completeLogin = async (tokens: TokenPair): Promise<void> => {
    if (activeRequestRef.current) return;
    const controller = new AbortController();
    activeRequestRef.current = controller;
    try {
      clearClientSession();
      setTokens(tokens);
      const user = await fetchMe(controller.signal);
      if (controller.signal.aborted || activeRequestRef.current !== controller) return;
      setUser(user);
      const context = getRouteAccessContext(user);
      const target =
        accessibleInternalPath(search.from, context) ?? firstAccessiblePath(context) ?? "/security";
      await navigate({ to: target });
    } finally {
      if (activeRequestRef.current === controller) activeRequestRef.current = null;
    }
  };

  const acceptLogin = async (result: LoginVerifyResponse): Promise<void> => {
    if (activeRequestRef.current) return;
    const flowVersion = flowVersionRef.current;
    setError(null);
    if ("access_token" in result) {
      try {
        await completeLogin(result);
      } catch (cause) {
        if (flowVersion === flowVersionRef.current)
          setError(describeApiError(cause, "Не удалось завершить вход. Проверьте соединение."));
      }
    } else {
      setEnrollmentVisible(result.status === "mfa_enrollment_required");
      setChallenge(result);
    }
  };

  const returnToLogin = () => {
    if (activeRequestRef.current) return;
    flowVersionRef.current += 1;
    setChallenge(null);
    setEnrollmentVisible(false);
    setError(null);
  };

  return (
    <LoginLayout publicWebsiteUrl={publicWebsiteUrl} wide={enrollmentVisible}>
      {challenge ? (
        <Suspense
          fallback={
            <div className="space-y-4">
              <p role="status" className="text-sm text-foreground-secondary">
                Загружаем подтверждение защиты…
              </p>
              <Button type="button" variant="ghost" onClick={returnToLogin}>
                Вернуться ко входу
              </Button>
            </div>
          }
        >
          <MfaLoginFlow
            key={challenge.challenge_token}
            challenge={challenge}
            onAuthenticated={completeLogin}
            onBack={returnToLogin}
            onEnrollment={() => setEnrollmentVisible(true)}
          />
        </Suspense>
      ) : (
        <>
          <LoginCredentialsForm onAuthenticated={acceptLogin} />
          {error && (
            <p
              role="alert"
              className="mt-4 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
            >
              {error}
            </p>
          )}
        </>
      )}
    </LoginLayout>
  );
}
