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
  getSale,
  openShift,
  updateSaleItem,
} from "./api";
import {
  type PaymentAddPayload,
  type PrescriptionLogPayload,
  type ShiftClosePayload,
  type ShiftOpenPayload,
} from "./types";

export const posKeys = {
  shift: (registerId: string) => ["pos", "shift", registerId] as const,
  sale: (saleId: string) => ["pos", "sale", saleId] as const,
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
  return useMutation({
    mutationFn: (registerId: string) => createSale(registerId),
  });
}

export function useAddSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; catalogId: string; qty: string }) =>
      addSaleItem(args.saleId, args.catalogId, args.qty),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
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
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
  });
}

export function useCompleteSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (saleId: string) => completeSale(saleId),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(data.id) });
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
