// Phase-1 token storage. CLAUDE.md explicitly notes that sensitive refresh
// tokens should move to httpOnly cookies in Phase 2 — until then both tokens
// live in localStorage, which is documented as an "осознанный риск".

import { type TokenPair } from "./types";

const ACCESS_KEY = "aurum.access_token";
const REFRESH_KEY = "aurum.refresh_token";

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadTokens(): { access: string | null; refresh: string | null } {
  const ls = safeLocalStorage();
  if (!ls) return { access: null, refresh: null };
  return {
    access: ls.getItem(ACCESS_KEY),
    refresh: ls.getItem(REFRESH_KEY),
  };
}

export function saveTokens(tokens: TokenPair): void {
  const ls = safeLocalStorage();
  if (!ls) return;
  ls.setItem(ACCESS_KEY, tokens.access_token);
  ls.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  const ls = safeLocalStorage();
  if (!ls) return;
  ls.removeItem(ACCESS_KEY);
  ls.removeItem(REFRESH_KEY);
}
