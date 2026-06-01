import { api } from "@/lib/api";
import { type ZReport } from "@/features/pos/types";

export async function getZReport(shiftId: string): Promise<ZReport> {
  const { data } = await api.get<ZReport>(`/shifts/${shiftId}/z-report`);
  return data;
}

/** Accountant sales summary over [from, to] (YYYY-MM-DD) as an XLSX blob. */
export async function getSalesSummaryXlsx(
  from: string,
  to: string,
  branchId?: string,
): Promise<Blob> {
  const { data } = await api.get<Blob>("/reports/sales-summary.xlsx", {
    params: { from, to, branch_id: branchId || undefined },
    responseType: "blob",
  });
  return data;
}
