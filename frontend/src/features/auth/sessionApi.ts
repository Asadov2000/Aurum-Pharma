import { api } from "@/lib/api";

import { type ActiveSession, type SessionListResponse, type SessionRevokeResponse } from "./types";

export async function listActiveSessions(): Promise<ActiveSession[]> {
  const { data } = await api.get<SessionListResponse>("/auth/sessions");
  return data.items;
}

export async function revokeActiveSession(sessionId: string): Promise<SessionRevokeResponse> {
  const { data } = await api.delete<SessionRevokeResponse>(`/auth/sessions/${sessionId}`);
  return data;
}

export async function revokeOtherSessions(): Promise<SessionRevokeResponse> {
  const { data } = await api.post<SessionRevokeResponse>("/auth/sessions/revoke-others");
  return data;
}
