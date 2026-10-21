import { type MeResponse } from "./types";

const presentationFields = new Set([
  "email",
  "full_name",
  "last_login_at",
  "tenant_name",
  "reason",
]);

function canonicalAuthorityValue(key: string, value: unknown): unknown {
  if (presentationFields.has(key)) return undefined;
  // All arrays in MeResponse contain strings (permissions, branches, capabilities).
  if (Array.isArray(value)) return [...new Set(value as unknown[])].sort();
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).sort(([left], [right]) =>
        left.localeCompare(right),
      ),
    );
  }
  return value;
}

/** Compare authority, not presentation fields or the order of server results. */
export function authorizationSnapshotKey(user: MeResponse | null): string {
  return JSON.stringify(user, canonicalAuthorityValue);
}
