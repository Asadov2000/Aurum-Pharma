import { useQuery } from "@tanstack/react-query";

import { getZReport } from "./api";

export const reportsKeys = {
  zReport: (shiftId: string) => ["reports", "z", shiftId] as const,
};

export function useZReportQuery(shiftId: string | null) {
  return useQuery({
    queryKey: reportsKeys.zReport(shiftId ?? ""),
    queryFn: () => getZReport(shiftId as string),
    enabled: shiftId !== null && shiftId !== "",
  });
}
