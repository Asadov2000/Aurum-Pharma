import { create } from "zustand";

import { clearTokens, loadTokens, saveTokens } from "@/features/auth/storage";
import { getAuthEpoch, isSessionRestorationBlocked } from "@/features/auth/logoutState";
import { tokenMatchesEpoch } from "@/features/auth/tokenEpoch";
import { type MeResponse, type TokenPair } from "@/features/auth/types";

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

// Token rotation retains the session; identity changes and logout invalidate work
// started under its previous authority, including a new login by the same user.
let sessionGeneration = 0;
useAuthStore.subscribe((state, previous) => {
  if (
    state.user?.id !== previous.user?.id ||
    state.user?.active_tenant_id !== previous.user?.active_tenant_id ||
    (previous.accessToken !== null && state.accessToken === null)
  ) {
    sessionGeneration += 1;
  }
});

export function getAuthSessionGeneration(): number {
  return sessionGeneration;
}

export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}
