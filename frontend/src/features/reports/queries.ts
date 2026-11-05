import { hashKey, keepPreviousData, useQuery } from "@tanstack/react-query";

import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { useAuth } from "@/features/auth/hooks";
import { type MeResponse } from "@/features/auth/types";
import { getAuthSessionGeneration } from "@/stores/auth";

import { useReportBranchScope } from "./branchScope";
import {
  getSalesSummary,
  getStockOnDate,
  getTopProducts,
  getZReport,
  listShiftHistory,
} from "./api";
import {
  type SalesSummaryParams,
  type ShiftHistoryParams,
  type StockOnDateParams,
  type TopProductsParams,
} from "./types";

export const reportsKeys = {
  zReport: (shiftId: string, accessKey: string) => ["reports", "z", shiftId, accessKey] as const,
  shifts: (params: ShiftHistoryParams) => ["reports", "shifts", params] as const,
  salesSummary: (params: SalesSummaryParams) => ["reports", "sales-summary", params] as const,
  topProducts: (params: TopProductsParams) => ["reports", "top-products", params] as const,
  stockOnDate: (params: StockOnDateParams) => ["reports", "stock-on-date", params] as const,
};

export function zReportAccessKey(user: MeResponse | null): string {
  return hashKey([
    getAuthSessionGeneration(),
    user?.id,
    user?.active_tenant_id,
    user?.is_developer,
    user?.permissions,
    user?.permission_scopes,
    user?.support_access,
  ]);
}

export function useZReportQuery(shiftId: string | null, branchId: string) {
  const { user } = useAuth();
  return useQuery({
    queryKey: reportsKeys.zReport(shiftId ?? "", zReportAccessKey(user)),
    queryFn: ({ signal }) => getZReport(shiftId as string, signal),
    enabled: Boolean(shiftId) && hasPermissionForBranch(user, "reports.view", branchId),
    gcTime: 0,
  });
}

export function useShiftHistoryQuery(params: ShiftHistoryParams) {
  const { user } = useAuth();
  const { canView } = useReportBranchScope();
  return useQuery({
    enabled: canView,
    queryKey: [...reportsKeys.shifts(params), zReportAccessKey(user)],
    queryFn: () => listShiftHistory(params),
    placeholderData: keepPreviousData,
  });
}

export function useSalesSummaryQuery(params: SalesSummaryParams) {
  const { canView } = useReportBranchScope();
  return useQuery({
    enabled: canView,
    queryKey: reportsKeys.salesSummary(params),
    queryFn: () => getSalesSummary(params),
    placeholderData: keepPreviousData,
  });
}

export function useTopProductsQuery(params: TopProductsParams) {
  const { canView } = useReportBranchScope();
  return useQuery({
    enabled: canView,
    queryKey: reportsKeys.topProducts(params),
    queryFn: () => getTopProducts(params),
    placeholderData: keepPreviousData,
  });
}

export function useStockOnDateQuery(params: StockOnDateParams) {
  const { canView } = useReportBranchScope();
  return useQuery({
    enabled: canView,
    queryKey: reportsKeys.stockOnDate(params),
    queryFn: () => getStockOnDate(params),
    placeholderData: keepPreviousData,
  });
}
