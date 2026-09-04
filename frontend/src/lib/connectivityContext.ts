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
  if (context) {
    return context;
  }
  const browserOnline = readNavigatorOnline();
  return {
    status: browserOnline ? "online" : "offline",
    canUseServer: browserOnline,
    checkNow: () => undefined,
  };
}

export function readNavigatorOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}
