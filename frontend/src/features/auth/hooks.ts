import { useCallback } from "react";

import { useAuthStore } from "@/stores/auth";

import { logoutRequest, verifyLoginCode } from "./api";
import { getPendingRefreshOperationId } from "./refreshOperation";
import { clearClientSession } from "./session";
import { type LoginVerifyRequest } from "./types";

export function useAuth() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const setTokens = useAuthStore((s) => s.setTokens);

  const login = useCallback(
    async (payload: LoginVerifyRequest, signal?: AbortSignal) => {
      signal?.throwIfAborted();
      const result = await verifyLoginCode(payload, signal);
      signal?.throwIfAborted();
      if ("access_token" in result) {
        clearClientSession();
        setTokens(result);
      }
      return result;
    },
    [setTokens],
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest(getPendingRefreshOperationId());
    } catch {
      // Whatever the server says, the client-side session is over.
    }
    clearClientSession();
  }, []);

  return {
    isAuthenticated: accessToken !== null,
    hydrated,
    user,
    login,
    logout,
  };
}
