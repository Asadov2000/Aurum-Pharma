import { create } from "zustand";

import { clearTokens, loadTokens, saveTokens } from "@/features/auth/storage";
import { getAuthEpoch, isSessionRestorationBlocked } from "@/features/auth/logoutState";
import { tokenMatchesEpoch } from "@/features/auth/tokenEpoch";
import { type MeResponse, type TokenPair } from "@/features/auth/types";
import { authorizationSnapshotKey } from "@/features/auth/authorizationSnapshot";
import { queryClient } from "@/lib/query";

interface AuthState {
  accessToken: string | null;
  user: MeResponse | null;
  hydrated: boolean;
  setTokens: (tokens: TokenPair) => void;
  setUser: (user: MeResponse | null) => void;
  clear: () => void;
}

const initial = loadTokens();

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: initial.access,
  user: null,
  hydrated: false,
  setTokens: (tokens) => {
    if (isSessionRestorationBlocked() || !tokenMatchesEpoch(tokens, getAuthEpoch())) return;
    saveTokens(tokens);
    set({
      accessToken: tokens.access_token,
      hydrated: true,
    });
  },
  setUser: (user) => set({ user, hydrated: true }),
  clear: () => {
    sessionGeneration += 1;
    clearTokens();
    set({ accessToken: null, user: null, hydrated: true });
  },
}));

// A new authority invalidates work started under the old one. Token rotation,
// profile edits and network outages do not revoke the current authority.
let sessionGeneration = 0;
useAuthStore.subscribe((state, previous) => {
  const authorityChanged =
    state.user !== previous.user &&
    authorizationSnapshotKey(state.user) !== authorizationSnapshotKey(previous.user);
  if (authorityChanged || (previous.accessToken !== null && state.accessToken === null)) {
    sessionGeneration += 1;
  }
  if (previous.user && authorityChanged) {
    // Keep the identity query that delivered the new authority; drop all data
    // and pending reads from the old scope before protected pages mount again.
    const filters = {
      predicate: (query: { queryKey: readonly unknown[] }) =>
        !(query.queryKey[0] === "auth" && query.queryKey[1] === "me"),
    };
    void queryClient.cancelQueries(filters);
    queryClient.removeQueries(filters);
    queryClient.getMutationCache().clear();
    window.dispatchEvent(new Event("aurum:client-session-cleared"));
  }
});

export function getAuthSessionGeneration(): number {
  return sessionGeneration;
}

export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}
