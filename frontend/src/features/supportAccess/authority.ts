import { CanceledError } from "axios";

import { getAuthEpoch } from "@/features/auth/logoutState";
import { getAuthSessionGeneration } from "@/stores/auth";
import { getSupportAccessRevision } from "@/stores/supportAccess";

export interface SupportAuthority {
  readonly generation: number;
  readonly epoch: string | null;
  readonly revision: number;
}

export function captureSupportAuthority(): SupportAuthority {
  return {
    generation: getAuthSessionGeneration(),
    epoch: getAuthEpoch(),
    revision: getSupportAccessRevision(),
  };
}

export function isSupportAuthorityCurrent(authority: SupportAuthority): boolean {
  return (
    authority.generation === getAuthSessionGeneration() &&
    authority.epoch === getAuthEpoch() &&
    authority.revision === getSupportAccessRevision()
  );
}

export function assertSupportAuthority(authority: SupportAuthority): void {
  if (!isSupportAuthorityCurrent(authority)) {
    throw new CanceledError("Support access session changed");
  }
}
