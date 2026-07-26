import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getZReport, listShiftHistory } from "./api";
import { type ShiftHistoryParams } from "./types";

export const reportsKeys = {
  zReport: (shiftId: string) => ["reports", "z", shiftId] as const,
  shifts: (params: ShiftHistoryParams) => ["reports", "shifts", params] as const,
};

export function useZReportQuery(shiftId: string | null) {
  return useQuery({
    queryKey: reportsKeys.zReport(shiftId ?? ""),
    queryFn: () => getZReport(shiftId as string),
    enabled: shiftId !== null && shiftId !== "",
  });
}

export function useShiftHistoryQuery(params: ShiftHistoryParams) {
  return useQuery({
    queryKey: reportsKeys.shifts(params),
    queryFn: () => listShiftHistory(params),
    placeholderData: keepPreviousData,
  });
}
