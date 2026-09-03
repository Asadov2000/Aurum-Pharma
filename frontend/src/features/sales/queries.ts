import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getActiveRefundAttempt, getSaleDetails, listSales, refundSale } from "./api";
import { type RefundPayload, type SaleSearchParams } from "./types";

export const salesKeys = {
  list: (params: SaleSearchParams) => ["sales", "list", params] as const,
  detail: (id: string) => ["sales", "detail", id] as const,
  activeRefundAttempt: (parentSaleId: string) =>
    ["sales", "refund-attempts", "active", parentSaleId] as const,
};

export function useSalesQuery(params: SaleSearchParams, enabled = true) {
  return useQuery({
    queryKey: salesKeys.list(params),
    queryFn: () => listSales(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSaleDetailsQuery(saleId: string | null) {
  return useQuery({
    queryKey: salesKeys.detail(saleId ?? ""),
    queryFn: () => getSaleDetails(saleId as string),
    enabled: saleId !== null,
  });
}

export function useActiveRefundAttemptQuery(parentSaleId: string, enabled: boolean) {
  return useQuery({
    queryKey: salesKeys.activeRefundAttempt(parentSaleId),
    queryFn: () => getActiveRefundAttempt(parentSaleId),
    enabled,
    retry: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  });
}

export function useRefundSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { parentSaleId: string; payload: RefundPayload }) =>
      refundSale(args.parentSaleId, args.payload),
    onSuccess: (returnSale, args) => {
      // The receipt list flips has_refund and a new return row appears;
      // batches get their qty restored.
      void qc.invalidateQueries({ queryKey: ["sales", "list"] });
      void qc.invalidateQueries({ queryKey: salesKeys.detail(args.parentSaleId) });
      void qc.invalidateQueries({ queryKey: salesKeys.detail(returnSale.id) });
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}
