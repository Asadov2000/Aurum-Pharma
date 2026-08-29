import { useSyncExternalStore } from "react";

function subscribe(onStatusChange: () => void): () => void {
  window.addEventListener("online", onStatusChange);
  window.addEventListener("offline", onStatusChange);

  return () => {
    window.removeEventListener("online", onStatusChange);
    window.removeEventListener("offline", onStatusChange);
  };
}

function getOnlineSnapshot(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}

export function useOnlineStatus(): boolean {
  return useSyncExternalStore(subscribe, getOnlineSnapshot, () => true);
}
