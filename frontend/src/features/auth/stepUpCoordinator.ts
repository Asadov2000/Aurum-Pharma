import { useSyncExternalStore } from "react";

type PendingStepUp = {
  promise: Promise<string | null>;
  resolve: (accessToken: string | null) => void;
};

const listeners = new Set<() => void>();
let pending: PendingStepUp | null = null;
let requested = false;

function emit(): void {
  for (const listener of listeners) listener();
}

export function requestMfaStepUp(): Promise<string | null> {
  if (pending) return pending.promise;

  let resolvePending: (accessToken: string | null) => void = () => {};
  const promise = new Promise<string | null>((resolve) => {
    resolvePending = resolve;
  });
  pending = { promise, resolve: resolvePending };
  requested = true;
  emit();
  return promise;
}

export function completeMfaStepUp(accessToken: string): void {
  const current = pending;
  pending = null;
  requested = false;
  emit();
  current?.resolve(accessToken);
}

export function cancelMfaStepUp(): void {
  const current = pending;
  pending = null;
  requested = false;
  emit();
  current?.resolve(null);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): boolean {
  return requested;
}

export function useMfaStepUpRequested(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
