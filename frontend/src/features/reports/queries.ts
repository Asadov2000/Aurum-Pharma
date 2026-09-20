import { keepPreviousData, useQuery } from "@tanstack/react-query";

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
  zReport: (shiftId: string) => ["reports", "z", shiftId] as const,
  shifts: (params: ShiftHistoryParams) => ["reports", "shifts", params] as const,
  salesSummary: (params: SalesSummaryParams) => ["reports", "sales-summary", params] as const,
  topProducts: (params: TopProductsParams) => ["reports", "top-products", params] as const,
  stockOnDate: (params: StockOnDateParams) => ["reports", "stock-on-date", params] as const,
};

export function useZReportQuery(shiftId: string | null) {
  return useQuery({
    queryKey: reportsKeys.zReport(shiftId ?? ""),
    queryFn: () => getZReport(shiftId as string),
    enabled: shiftId !== null && shiftId !== "",
  });
}

export function useShiftHistoryQuery(params: ShiftHistoryParams) {
  const { canView } = useReportBranchScope();
  return useQuery({
    enabled: canView,
    queryKey: reportsKeys.shifts(params),
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
