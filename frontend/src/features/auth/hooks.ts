import { useCallback } from "react";

import { useAuthStore } from "@/stores/auth";

import { logoutLocally } from "./logout";

export function useAuth() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);

  const logout = useCallback(logoutLocally, []);

  return {
    isAuthenticated: accessToken !== null,
    hydrated,
    user,
    logout,
  };
}
