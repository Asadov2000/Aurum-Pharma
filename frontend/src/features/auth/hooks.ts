import { useCallback } from "react";

import { useAuthStore } from "@/stores/auth";

import { logoutRequest, verifyLoginCode } from "./api";
import { type LoginVerifyRequest } from "./types";

export function useAuth() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const setTokens = useAuthStore((s) => s.setTokens);
  const clear = useAuthStore((s) => s.clear);

  const login = useCallback(
    async (payload: LoginVerifyRequest) => {
      const tokens = await verifyLoginCode(payload);
      setTokens(tokens);
      return tokens;
    },
    [setTokens],
  );

  const logout = useCallback(async () => {
    if (refreshToken) {
      try {
        await logoutRequest(refreshToken);
      } catch {
        // Whatever the server says, the client-side session is over.
      }
    }
    clear();
  }, [refreshToken, clear]);

  return {
    isAuthenticated: accessToken !== null,
    hydrated,
    user,
    login,
    logout,
  };
}
