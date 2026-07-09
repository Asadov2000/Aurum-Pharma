import { create } from "zustand";

import { clearTokens, loadTokens, saveTokens } from "@/features/auth/storage";
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
    saveTokens(tokens);
    set({
      accessToken: tokens.access_token,
      hydrated: true,
    });
  },
  setUser: (user) => set({ user, hydrated: true }),
  clear: () => {
    clearTokens();
    set({ accessToken: null, user: null, hydrated: true });
  },
}));

export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}
