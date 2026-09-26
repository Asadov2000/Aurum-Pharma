// Tokens and their authority stay in memory; epochs contain no credentials.
const epochs = new WeakMap<object, string | null>();

export function rememberTokenEpoch<T>(value: T, epoch: string | null): T {
  if (typeof value === "object" && value !== null && "access_token" in value) {
    epochs.set(value, epoch);
  }
  return value;
}

export function tokenMatchesEpoch(value: object, epoch: string | null): boolean {
  return !epochs.has(value) || epochs.get(value) === epoch;
}
