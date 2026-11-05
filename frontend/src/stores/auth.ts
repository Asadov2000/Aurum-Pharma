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
let sessionGeneration = 0;
let identityGeneration = 0;

export const useAuthStore = create<AuthState>((set, get) => ({
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
  setUser: (user) => {
    const previous = get().user;
    if (
      user?.id !== previous?.id ||
      user?.active_tenant_id !== previous?.active_tenant_id ||
      user?.home_tenant_id !== previous?.home_tenant_id
    ) {
      identityGeneration += 1;
    }
    if (
      user !== previous &&
      authorizationSnapshotKey(user) !== authorizationSnapshotKey(previous)
    ) {
      sessionGeneration += 1;
      if (previous) {
        // Retain the identity result that delivered the new permissions.
        queryClient.removeQueries({
          predicate: ({ queryKey }: { queryKey: readonly unknown[] }) =>
            queryKey[0] !== "auth" || queryKey[1] !== "me",
        });
        queryClient.getMutationCache().clear();
        window.dispatchEvent(new Event("aurum:client-session-cleared"));
      }
    }
    set({ user, hydrated: true });
  },
  clear: () => {
    sessionGeneration += 1;
    identityGeneration += 1;
    clearTokens();
    set({ accessToken: null, user: null, hydrated: true });
  },
}));

export function getAuthSessionGeneration(): number {
  return sessionGeneration;
}

export function getAuthIdentityGeneration(): number {
  return identityGeneration;
}

export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}
