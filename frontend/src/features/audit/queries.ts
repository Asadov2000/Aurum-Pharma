import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { searchAudit } from "./api";
import { type AuditSearchParams } from "./types";

export const auditKeys = {
  search: (params: AuditSearchParams) => ["audit", params] as const,
};

export function useAuditQuery(params: AuditSearchParams, enabled = true) {
  return useQuery({
    queryKey: auditKeys.search(params),
    queryFn: () => searchAudit(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}
