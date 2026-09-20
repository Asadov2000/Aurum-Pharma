import { useSyncExternalStore } from "react";

import { type EdgeSnapshotMetadata } from "./types";

type Listener = () => void;

let metadata: EdgeSnapshotMetadata | null = null;
const listeners = new Set<Listener>();

if (typeof window !== "undefined") {
  window.addEventListener("aurum:client-session-cleared", clearEdgeSnapshotMetadata);
}

export function recordEdgeSnapshotMetadata(next: EdgeSnapshotMetadata): void {
  if (
    metadata?.snapshot_id === next.snapshot_id &&
    metadata.last_confirmed_at === next.last_confirmed_at &&
    metadata.is_stale === next.is_stale
  ) {
    return;
  }
  metadata = next;
  listeners.forEach((listener) => listener());
}

export function clearEdgeSnapshotMetadata(): void {
  if (metadata === null) return;
  metadata = null;
  listeners.forEach((listener) => listener());
}

export function useEdgeSnapshotMetadata(): EdgeSnapshotMetadata | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): EdgeSnapshotMetadata | null {
  return metadata;
}
