import { api } from "@/lib/api";
import { type ZReport } from "@/features/pos/types";

import {
  type SalesSummaryOverview,
  type SalesSummaryParams,
  type ShiftHistoryList,
  type ShiftHistoryParams,
  type StockOnDateOverview,
  type StockOnDateParams,
  type TopProductsOverview,
  type TopProductsParams,
} from "./types";

export async function getZReport(shiftId: string): Promise<ZReport> {
  const { data } = await api.get<ZReport>(`/shifts/${shiftId}/z-report`);
  return data;
}

export async function listShiftHistory(params: ShiftHistoryParams): Promise<ShiftHistoryList> {
  const { data } = await api.get<ShiftHistoryList>("/shifts", { params });
  return data;
}

export async function getSalesSummary(params: SalesSummaryParams): Promise<SalesSummaryOverview> {
  const { data } = await api.get<SalesSummaryOverview>("/reports/sales-summary", { params });
  return data;
}

export async function getTopProducts(params: TopProductsParams): Promise<TopProductsOverview> {
  const { data } = await api.get<TopProductsOverview>("/reports/top-products", { params });
  return data;
}

export async function getStockOnDate(params: StockOnDateParams): Promise<StockOnDateOverview> {
  const { data } = await api.get<StockOnDateOverview>("/reports/stock-on-date", { params });
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

/** Stock on a date (YYYY-MM-DD) as an XLSX blob. */
export async function getStockOnDateXlsx(date: string, branchId?: string): Promise<Blob> {
  const { data } = await api.get<Blob>("/reports/stock-on-date.xlsx", {
    params: { date, branch_id: branchId || undefined },
    responseType: "blob",
  });
  return data;
}
