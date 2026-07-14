import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addPayment,
  addPrescription,
  addSaleItem,
  closeShift,
  completeSale,
  createSale,
  deleteSaleItem,
  getCurrentShift,
  getReceipt,
  getSale,
  openShift,
  updateSaleItem,
} from "./api";
import {
  type PaymentAddPayload,
  type PrescriptionLogPayload,
  type SaleDetails,
  type ShiftClosePayload,
  type ShiftOpenPayload,
} from "./types";

export const posKeys = {
  shift: (registerId: string) => ["pos", "shift", registerId] as const,
  sale: (saleId: string) => ["pos", "sale", saleId] as const,
  receipt: (saleId: string) => ["pos", "receipt", saleId] as const,
};

// ---- shift ----

export function useCurrentShiftQuery(registerId: string | null) {
  return useQuery({
    queryKey: posKeys.shift(registerId ?? ""),
    queryFn: () => getCurrentShift(registerId as string),
    enabled: registerId !== null && registerId !== "",
  });
}

export function useOpenShift() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ShiftOpenPayload) => openShift(payload),
    onSuccess: (data) => {
      qc.setQueryData(posKeys.shift(data.register_id), data);
    },
  });
}

export function useCloseShift() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { shiftId: string; registerId: string; payload: ShiftClosePayload }) =>
      closeShift(args.shiftId, args.payload),
    onSuccess: (_data, vars) => {
      // Closed shift is no longer "current" — null it out.
      qc.setQueryData(posKeys.shift(vars.registerId), null);
    },
  });
}

// ---- sale ----

export function useSaleQuery(saleId: string | null) {
  return useQuery({
    queryKey: posKeys.sale(saleId ?? ""),
    queryFn: () => getSale(saleId as string),
    enabled: saleId !== null && saleId !== "",
  });
}

export function useCreateSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (registerId: string) => createSale(registerId),
    onSuccess: (data) => {
      const draft: SaleDetails = { ...data, items: [], payments: [] };
      qc.setQueryData(posKeys.sale(data.id), draft);
    },
  });
}

export function useReceiptQuery(saleId: string | null) {
  return useQuery({
    queryKey: posKeys.receipt(saleId ?? ""),
    queryFn: () => getReceipt(saleId as string),
    enabled: saleId !== null && saleId !== "",
  });
}

export function useAddSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; catalogId: string; qty: string }) =>
      addSaleItem(args.saleId, args.catalogId, args.qty),
    onSuccess: (_data, vars) =>
      qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId), exact: true }),
  });
}

export function useUpdateSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; itemId: string; qty: string }) =>
      updateSaleItem(args.saleId, args.itemId, args.qty),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
  });
}

export function useDeleteSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; itemId: string }) =>
      deleteSaleItem(args.saleId, args.itemId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
  });
}

export function useAddPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; payload: PaymentAddPayload }) =>
      addPayment(args.saleId, args.payload),
    onSuccess: (payment, vars) => {
      qc.setQueryData<SaleDetails>(posKeys.sale(vars.saleId), (sale) => {
        if (!sale || sale.payments.some((item) => item.id === payment.id)) return sale;
        return { ...sale, payments: [...sale.payments, payment] };
      });
    },
  });
}

export function useCompleteSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (saleId: string) => completeSale(saleId),
    onSuccess: (data) => {
      qc.setQueryData<SaleDetails>(posKeys.sale(data.id), (sale) =>
        sale ? { ...sale, ...data } : sale,
      );
      // Completing affects inventory.
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}

export function useAddPrescription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; payload: PrescriptionLogPayload }) =>
      addPrescription(args.saleId, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
  });
}
