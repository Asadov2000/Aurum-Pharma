import { useCallback } from "react";

import { useAuthStore } from "@/stores/auth";

import { logoutRequest } from "./api";
import { getPendingRefreshOperationId } from "./refreshOperation";
import { clearClientSession } from "./session";

export function useAuth() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);

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
    logout,
  };
}
