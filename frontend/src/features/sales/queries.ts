import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSaleDetails, listSales, refundSale } from "./api";
import { type RefundPayload, type SaleSearchParams } from "./types";

export const salesKeys = {
  list: (params: SaleSearchParams) => ["sales", "list", params] as const,
  detail: (id: string) => ["sales", "detail", id] as const,
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

export function useRefundSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { parentSaleId: string; payload: RefundPayload }) =>
      refundSale(args.parentSaleId, args.payload),
    onSuccess: () => {
      // The receipt list flips has_refund and a new return row appears;
      // batches get their qty restored.
      void qc.invalidateQueries({ queryKey: ["sales", "list"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}
