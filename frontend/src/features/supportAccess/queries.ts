import { useMutation, useQuery } from "@tanstack/react-query";

import {
  listSupportCapabilities,
  listSupportSessions,
  revokeSupportSession,
  startSupportSession,
} from "./api";
import { type SupportAccessSessionCreate } from "./types";
import { assertSupportAuthority, type SupportAuthority } from "./authority";

export const supportAccessKeys = {
  capabilities: ["support-access", "capabilities"] as const,
  sessions: ["support-access", "sessions"] as const,
};

export function useSupportCapabilities(enabled = true) {
  return useQuery({
    queryKey: supportAccessKeys.capabilities,
    queryFn: listSupportCapabilities,
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useSupportSessions(enabled = true) {
  return useQuery({
    queryKey: supportAccessKeys.sessions,
    queryFn: listSupportSessions,
    enabled,
    staleTime: 10_000,
  });
}

export function useStartSupportSession() {
  return useMutation({
    mutationFn: (request: { payload: SupportAccessSessionCreate; authority: SupportAuthority }) => {
      assertSupportAuthority(request.authority);
      return startSupportSession(request.payload);
    },
  });
}

export function useRevokeSupportSession() {
  return useMutation({
    mutationFn: (request: { sessionId: string; authority: SupportAuthority }) => {
      assertSupportAuthority(request.authority);
      return revokeSupportSession(request.sessionId);
    },
  });
}
