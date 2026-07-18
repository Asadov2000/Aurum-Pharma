import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";

export function clearClientSession(): void {
  queryClient.clear();
  useAuthStore.getState().clear();
}
