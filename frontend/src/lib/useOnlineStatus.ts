import { useConnectivity } from "@/lib/connectivityContext";

export function useOnlineStatus(): boolean {
  return useConnectivity().canUseServer;
}
