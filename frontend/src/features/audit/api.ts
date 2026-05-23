import { api } from "@/lib/api";

import { type AuditPage, type AuditSearchParams } from "./types";

export async function searchAudit(params: AuditSearchParams): Promise<AuditPage> {
  const base = `/audit/${params.scope}`;
  const { data } = await api.get<AuditPage>(base, {
    params: {
      action: params.action || undefined,
      table_name: params.table_name || undefined,
      user_id: params.user_id || undefined,
      tenant_id: params.scope === "global" ? params.tenant_id || undefined : undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  });
  return data;
}
