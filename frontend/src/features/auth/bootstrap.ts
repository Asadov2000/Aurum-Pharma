// Wires the axios instance up to the auth store. Called once from main.tsx
// before the router renders, so any in-flight 401 can ask the store for a
// fresh refresh token instead of going through an empty closure.

import { configureAuthHooks } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { refreshTokensRequest } from "./api";

async function refreshSessionFromCookie(): Promise<string | null> {
  try {
    const tokens = await refreshTokensRequest();
    useAuthStore.getState().setTokens(tokens);
    return tokens.access_token;
  } catch {
    useAuthStore.getState().clear();
    return null;
  }
}

export function bootstrapAuth(): void {
  configureAuthHooks({
    getAccessToken: () => useAuthStore.getState().accessToken,
    refreshTokens: refreshSessionFromCookie,
    onAuthFailure: () => {
      useAuthStore.getState().clear();
    },
  });
  void refreshSessionFromCookie();
}
