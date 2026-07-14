import { type TokenPair } from "./types";
import { clearRefreshOperationId } from "./refreshOperation";

const LEGACY_ACCESS_KEY = "aurum.access_token";
const LEGACY_REFRESH_KEY = "aurum.refresh_token";

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function clearLegacyTokens(): void {
  const ls = safeLocalStorage();
  if (!ls) return;
  ls.removeItem(LEGACY_ACCESS_KEY);
  ls.removeItem(LEGACY_REFRESH_KEY);
}

export function loadTokens(): { access: string | null } {
  clearLegacyTokens();
  return { access: null };
}

export function saveTokens(_tokens: TokenPair): void {
  clearLegacyTokens();
  clearRefreshOperationId();
}

export function clearTokens(): void {
  clearLegacyTokens();
  clearRefreshOperationId();
}
