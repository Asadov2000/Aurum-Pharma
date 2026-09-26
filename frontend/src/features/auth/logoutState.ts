import { generateUuidV4 } from "./refreshOperation";

export interface LogoutState {
  id: string;
  phase: "pending" | "confirmed" | "signed-in";
  operationId: string | null;
}

interface LogoutConfirmation {
  confirmedLogoutId: string;
}

interface ExplicitSignIn {
  id: string;
  acknowledgedLogoutId: string | null;
}

const STORAGE_KEY = "aurum.auth_logout.v1";
const CONFIRMATION_KEY = `${STORAGE_KEY}.confirmation`;
const SIGN_IN_KEY = `${STORAGE_KEY}.signin`;
const STATE_KEYS = new Set([STORAGE_KEY, CONFIRMATION_KEY, SIGN_IN_KEY]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const BLOCKED_EPOCH = "blocked";
const listeners = new Set<() => void>();
let memoryIntent: LogoutState | null = null;
let persistenceFailed = false;
let channel: BroadcastChannel | null = null;
let probeSequence = 0;
let persistenceAvailable: boolean | null = null;

function cookieName(key: string): string {
  const protocol = window.location.protocol === "https:" ? "https" : "http";
  const port = window.location.port || (protocol === "https" ? "443" : "80");
  return `${key.replaceAll(".", "_")}_${protocol}_${port}`;
}

function readCookie(name: string): string | null {
  const matches = document.cookie
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${name}=`));
  if (matches.length > 1) throw new Error("Ambiguous logout marker");
  const match = matches[0];
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

function writeCookie(name: string, value: string, maxAge = 31_536_000): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; SameSite=Strict; Max-Age=${maxAge}${secure}`;
}

function parseRecord(value: string, keys: string[]): Record<string, unknown> | null {
  try {
    if (value.length > 512) return null;
    const record: unknown = JSON.parse(value);
    if (typeof record !== "object" || record === null || Array.isArray(record)) {
      return null;
    }
    const fields = record as Record<string, unknown>;
    return Object.keys(fields).length === keys.length &&
      keys.every((key) => Object.hasOwn(fields, key))
      ? fields
      : null;
  } catch {
    return null;
  }
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function parseIntent(value: string): LogoutState | null {
  const record = parseRecord(value, ["id", "phase", "operationId"]);
  return record !== null &&
    isUuid(record.id) &&
    record.phase === "pending" &&
    (record.operationId === null || isUuid(record.operationId))
    ? { id: record.id, phase: "pending", operationId: record.operationId }
    : null;
}

function parseConfirmation(value: string): LogoutConfirmation | null {
  const record = parseRecord(value, ["confirmedLogoutId"]);
  return record !== null && isUuid(record.confirmedLogoutId)
    ? { confirmedLogoutId: record.confirmedLogoutId }
    : null;
}

function parseSignIn(value: string): ExplicitSignIn | null {
  const record = parseRecord(value, ["id", "acknowledgedLogoutId"]);
  return record !== null &&
    isUuid(record.id) &&
    (record.acknowledgedLogoutId === null || isUuid(record.acknowledgedLogoutId))
    ? { id: record.id, acknowledgedLogoutId: record.acknowledgedLogoutId }
    : null;
}

function blockedState(intent: LogoutState | null = memoryIntent): LogoutState {
  return {
    id: BLOCKED_EPOCH,
    phase: "pending",
    operationId: intent?.operationId ?? null,
  };
}

function readCopies<T>(
  key: string,
  parse: (value: string) => T | null,
): {
  value: T | null;
  invalid: boolean;
  incomplete: boolean;
} {
  let storedValue: string | null = null;
  let cookieValue: string | null = null;
  let invalid = false;
  try {
    storedValue = window.localStorage.getItem(key);
  } catch {
    invalid = true;
  }
  try {
    cookieValue = readCookie(cookieName(key));
  } catch {
    invalid = true;
  }
  const stored = storedValue === null ? null : parse(storedValue);
  const cookie = cookieValue === null ? null : parse(cookieValue);
  if (
    (storedValue !== null && stored === null) ||
    (cookieValue !== null && cookie === null) ||
    (stored !== null && cookie !== null && JSON.stringify(stored) !== JSON.stringify(cookie)) ||
    (key !== STORAGE_KEY && (stored === null) !== (cookie === null))
  ) {
    invalid = true;
  }
  // A surviving logout intent remains restrictive; a positive sign-in or a
  // confirmation requires matching copies on both persistence surfaces.
  return {
    value: cookie ?? stored,
    invalid,
    incomplete: (stored === null) !== (cookie === null),
  };
}

// Readable storage may still reject writes (quota/privacy policy). A positive
// restoration decision requires actual set/get/remove and cookie verification.
function probePersistence(): boolean {
  const probeKey = `${STORAGE_KEY}.probe`;
  const probeValue = String(++probeSequence);
  try {
    const storage = window.localStorage;
    storage.setItem(probeKey, probeValue);
    if (storage.getItem(probeKey) !== probeValue) return false;
    storage.removeItem(probeKey);
    if (storage.getItem(probeKey) !== null) return false;
    const name = cookieName(probeKey);
    writeCookie(name, probeValue, 60);
    if (readCookie(name) !== probeValue) return false;
    writeCookie(name, "", 0);
    return readCookie(name) === null;
  } catch {
    return false;
  }
}

function canPersist(): boolean {
  if (persistenceAvailable === null) persistenceAvailable = probePersistence();
  return persistenceAvailable;
}

function readSnapshot(): { state: LogoutState | null; logoutId: string | null } {
  const intent = readCopies(STORAGE_KEY, parseIntent);
  const confirmation = readCopies(CONFIRMATION_KEY, parseConfirmation);
  const signIn = readCopies(SIGN_IN_KEY, parseSignIn);
  const snapshot = (state: LogoutState | null) => ({
    state,
    logoutId: intent.value?.id ?? null,
  });
  if (persistenceFailed || intent.invalid || confirmation.invalid || signIn.invalid) {
    return snapshot(blockedState(intent.value));
  }
  if (intent.value !== null) {
    if (signIn.value?.acknowledgedLogoutId !== intent.value.id) {
      return snapshot({
        ...intent.value,
        phase: confirmation.value?.confirmedLogoutId === intent.value.id ? "confirmed" : "pending",
      });
    }
    if (intent.incomplete) return snapshot(blockedState(intent.value));
  } else if (
    confirmation.value !== null ||
    signIn.value?.acknowledgedLogoutId != null ||
    memoryIntent !== null
  ) {
    return snapshot(blockedState());
  }
  if (!canPersist()) return snapshot(blockedState(intent.value));
  return snapshot(
    signIn.value === null ? null : { id: signIn.value.id, phase: "signed-in", operationId: null },
  );
}

export function readLogoutState(): LogoutState | null {
  return readSnapshot().state;
}

export function getAuthEpoch(): string | null {
  return readLogoutState()?.id ?? null;
}

export function isSessionRestorationBlocked(): boolean {
  const state = readLogoutState();
  return state !== null && state.phase !== "signed-in";
}

function persist(key: string, record: LogoutState | LogoutConfirmation | ExplicitSignIn): boolean {
  const value = JSON.stringify(record);
  let stored: boolean;
  let cookie: boolean;
  // Attempt the fallback even after a storage failure so logout intent can
  // survive reload; failed persistence never produces a positive decision.
  try {
    window.localStorage.setItem(key, value);
    stored = window.localStorage.getItem(key) === value;
  } catch {
    stored = false;
  }
  try {
    const name = cookieName(key);
    writeCookie(name, value);
    cookie = readCookie(name) === value;
  } catch {
    cookie = false;
  }
  persistenceFailed = !stored || !cookie;
  persistenceAvailable = !persistenceFailed;
  return !persistenceFailed;
}

function notifyListeners(): void {
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch {
      // One subscriber must not prevent the remaining consumers from observing
      // a security-state change.
    }
  }
}

function notifyChange(): void {
  notifyListeners();
  try {
    channel?.postMessage("changed");
  } catch {
    // Storage events and focus/pageshow remain available without broadcasting.
  }
}

export function beginLogout(operationId: string | null): LogoutState {
  const state: LogoutState = {
    id: generateUuidV4(),
    phase: "pending",
    operationId: isUuid(operationId) ? operationId : null,
  };
  memoryIntent = state;
  persist(STORAGE_KEY, state);
  notifyChange();
  return { ...state };
}

export function confirmLogout(id: string): void {
  const state = readLogoutState();
  if (state?.id !== id || state.phase !== "pending" || id === BLOCKED_EPOCH) return;
  persist(CONFIRMATION_KEY, { confirmedLogoutId: id });
  notifyChange();
}

// Login acknowledges only the observed intent; it never overwrites that intent.
// A concurrent new logout therefore wins even without localStorage CAS.
export function markExplicitLogin(
  expectedEpoch: string | null,
  recordEpoch?: (epoch: string) => void,
): boolean {
  const snapshot = readSnapshot();
  if (expectedEpoch === BLOCKED_EPOCH || (snapshot.state?.id ?? null) !== expectedEpoch) {
    return false;
  }
  const state: ExplicitSignIn = {
    id: generateUuidV4(),
    acknowledgedLogoutId: snapshot.logoutId,
  };
  if (!persist(SIGN_IN_KEY, state)) {
    notifyChange();
    return false;
  }
  const current = readLogoutState();
  const accepted = current?.phase === "signed-in" && current.id === state.id;
  if (accepted) recordEpoch?.(state.id);
  notifyChange();
  return accepted;
}

function onStorage(event: StorageEvent): void {
  if (event.key === null || STATE_KEYS.has(event.key)) notifyListeners();
}

function onBroadcast(event: MessageEvent<unknown>): void {
  if (event.data === "changed") notifyListeners();
}

export function subscribeLogoutState(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", notifyListeners);
    window.addEventListener("pageshow", notifyListeners);
    try {
      channel = new BroadcastChannel(STORAGE_KEY);
      channel.addEventListener("message", onBroadcast);
    } catch {
      channel = null;
    }
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size !== 0) return;
    window.removeEventListener("storage", onStorage);
    window.removeEventListener("focus", notifyListeners);
    window.removeEventListener("pageshow", notifyListeners);
    channel?.close();
    channel = null;
  };
}
