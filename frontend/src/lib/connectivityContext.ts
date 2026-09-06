import { createContext, useContext } from "react";

export type ConnectivityStatus = "checking" | "online" | "offline" | "server-unavailable";

export interface ConnectivityState {
  readonly status: ConnectivityStatus;
  readonly canUseServer: boolean;
  readonly checkNow: () => void;
}

export const ConnectivityContext = createContext<ConnectivityState | null>(null);

export function useConnectivity(): ConnectivityState {
  const context = useContext(ConnectivityContext);
  if (context) return context;

  const online = readNavigatorOnline();
  return {
    status: online ? "online" : "offline",
    canUseServer: online,
    checkNow: () => undefined,
  };
}

export function readNavigatorOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine;
}
