import { api, withAuthSession } from "@/lib/api";

import { assertSupportAuthority, captureSupportAuthority } from "./authority";

import {
  type SupportAccessSession,
  type SupportAccessSessionCreate,
  type SupportAccessSessionList,
  type SupportCapability,
} from "./types";

export async function listSupportCapabilities(): Promise<SupportCapability[]> {
  const { data } = await api.get<SupportCapability[]>("/admin/support-access/capabilities");
  return data;
}

export async function listSupportSessions(): Promise<SupportAccessSession[]> {
  const { data } = await api.get<SupportAccessSessionList>("/admin/support-access/sessions");
  return data.items;
}

export async function startSupportSession(
  payload: SupportAccessSessionCreate,
): Promise<SupportAccessSession> {
  const authority = captureSupportAuthority();
  const { data } = await api.post<SupportAccessSession>(
    "/admin/support-access/sessions",
    payload,
    withAuthSession(undefined, true),
  );
  assertSupportAuthority(authority);
  return data;
}

export async function revokeSupportSession(sessionId: string): Promise<void> {
  await api.delete(`/admin/support-access/sessions/${sessionId}`, withAuthSession());
}
