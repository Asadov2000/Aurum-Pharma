import { fetchMe } from "@/features/auth/api";
import { meQueryKey } from "@/features/auth/queries";
import { clearClientSupportContext } from "@/features/auth/session";
import { type MeResponse } from "@/features/auth/types";
import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";
import { useSupportAccessStore } from "@/stores/supportAccess";

import { revokeSupportSession } from "./api";
import { type SupportAccessSession } from "./types";

async function fetchFreshIdentity(): Promise<MeResponse> {
  await queryClient.cancelQueries();
  queryClient.clear();
  return fetchMe();
}

function commitIdentity(user: MeResponse | null): void {
  queryClient.clear();
  if (user) {
    queryClient.setQueryData(meQueryKey, user);
  }
  useAuthStore.getState().setUser(user);
}

function isExpectedContext(user: MeResponse, session: SupportAccessSession): boolean {
  return (
    user.id === session.actor_user_id &&
    user.active_tenant_id === session.tenant_id &&
    user.support_access?.id === session.id &&
    user.support_access.tenant_id === session.tenant_id
  );
}

async function revokeQuietly(sessionId: string): Promise<void> {
  try {
    await revokeSupportSession(sessionId);
  } catch {
    // The local privilege context is removed even if the network is unavailable.
  }
}

async function reloadIdentity(): Promise<void> {
  const user = await fetchFreshIdentity();
  queryClient.setQueryData(meQueryKey, user);
  useAuthStore.getState().setUser(user);
}

export async function activateSupportContext(session: SupportAccessSession): Promise<void> {
  const previousUser = useAuthStore.getState().user;
  useSupportAccessStore.getState().setActive(session);
  try {
    const user = await fetchFreshIdentity();
    if (!isExpectedContext(user, session)) {
      throw new Error("Support access identity mismatch");
    }
    commitIdentity(user);
  } catch (error) {
    useSupportAccessStore.getState().clear();
    await revokeQuietly(session.id);
    commitIdentity(previousUser);
    throw error;
  }
}

export async function clearSupportContext(): Promise<void> {
  clearClientSupportContext();
  await reloadIdentity();
}
