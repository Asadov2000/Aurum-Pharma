import { fetchMe } from "@/features/auth/api";
import { meQueryKey } from "@/features/auth/queries";
import { clearClientSupportContext } from "@/features/auth/session";
import { type MeResponse } from "@/features/auth/types";
import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";
import { useSupportAccessStore } from "@/stores/supportAccess";

import { revokeSupportSession } from "./api";
import {
  assertSupportAuthority,
  captureSupportAuthority,
  isSupportAuthorityCurrent,
  type SupportAuthority,
} from "./authority";
import { type SupportAccessSession } from "./types";

export class SupportContextFailure extends Error {
  constructor(
    readonly original: unknown,
    readonly authority: SupportAuthority,
  ) {
    super(original instanceof Error ? original.message : "Support access failed");
  }
}

async function fetchFreshIdentity(authority: SupportAuthority): Promise<MeResponse> {
  assertSupportAuthority(authority);
  await queryClient.cancelQueries();
  assertSupportAuthority(authority);
  queryClient.clear();
  const user = await fetchMe();
  assertSupportAuthority(authority);
  return user;
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

export async function activateSupportContext(
  session: SupportAccessSession,
): Promise<SupportAuthority> {
  const previousUser = useAuthStore.getState().user;
  if (previousUser?.id !== session.actor_user_id) {
    throw new Error("Support access actor mismatch");
  }
  useSupportAccessStore.getState().setActive(session);
  const authority = captureSupportAuthority();
  try {
    const user = await fetchFreshIdentity(authority);
    if (!isExpectedContext(user, session)) {
      throw new Error("Support access identity mismatch");
    }
    commitIdentity(user);
    return captureSupportAuthority();
  } catch (error) {
    if (!isSupportAuthorityCurrent(authority)) throw error;
    // Remove local elevation before waiting for the network. Capture only our
    // synchronous transition; never adopt authority discovered after an await.
    clearClientSupportContext();
    const rollbackAuthority = captureSupportAuthority();
    await revokeQuietly(session.id);
    if (isSupportAuthorityCurrent(rollbackAuthority)) {
      if (!previousUser?.support_access) commitIdentity(previousUser);
      throw new SupportContextFailure(error, captureSupportAuthority());
    }
    throw error;
  }
}

export async function clearSupportContext(): Promise<SupportAuthority> {
  clearClientSupportContext();
  const authority = captureSupportAuthority();
  const user = await fetchFreshIdentity(authority);
  commitIdentity(user);
  return captureSupportAuthority();
}
