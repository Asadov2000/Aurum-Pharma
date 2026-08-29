import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addPayment,
  addPosFavorite,
  addPrescription,
  addSaleItem,
  beginPaymentAttemptReconciliation,
  checkoutSale,
  closeShift,
  completeSale,
  confirmPaymentAttempt,
  createPaymentAttempt,
  createSale,
  deleteSaleItem,
  getCurrentShift,
  getPosFavorites,
  getReceipt,
  getSale,
  openShift,
  removePosFavorite,
  updateSaleItem,
  voidPaymentAttempt,
} from "./api";
import {
  type PaymentAttemptConfirmPayload,
  type PaymentAttemptCreatePayload,
  type PaymentAttemptVoidPayload,
  type PaymentAddPayload,
  type PrescriptionLogPayload,
  type SaleCheckoutPayload,
  type SaleCheckoutResult,
  type SaleDetails,
  type ShiftClosePayload,
  type ShiftOpenPayload,
} from "./types";

export const posKeys = {
  favoritesRoot: ["pos", "favorites"] as const,
  favorites: (branchId?: string) => ["pos", "favorites", branchId ?? "all"] as const,
  shift: (registerId: string) => ["pos", "shift", registerId] as const,
  sale: (saleId: string) => ["pos", "sale", saleId] as const,
  receipt: (saleId: string) => ["pos", "receipt", saleId] as const,
};

export function usePosFavoritesQuery(branchId?: string) {
  return useQuery({
    queryKey: posKeys.favorites(branchId),
    queryFn: () => getPosFavorites(branchId as string),
    enabled: Boolean(branchId),
  });
}

export function useAddPosFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalogId: string) => addPosFavorite(catalogId),
    onSuccess: () => qc.invalidateQueries({ queryKey: posKeys.favoritesRoot }),
  });
}

export function useRemovePosFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalogId: string) => removePosFavorite(catalogId),
    onSuccess: () => qc.invalidateQueries({ queryKey: posKeys.favoritesRoot }),
  });
}

export function useCreatePaymentAttempt() {
  return useMutation({
    mutationFn: (payload: PaymentAttemptCreatePayload) => createPaymentAttempt(payload),
  });
}

export function useConfirmPaymentAttempt() {
  return useMutation({
    mutationFn: (args: { attemptId: string; payload?: PaymentAttemptConfirmPayload }) =>
      confirmPaymentAttempt(args.attemptId, args.payload),
  });
}

export function useBeginPaymentAttemptReconciliation() {
  return useMutation({
    mutationFn: (attemptId: string) => beginPaymentAttemptReconciliation(attemptId),
  });
}

export function useVoidPaymentAttempt() {
  return useMutation({
    mutationFn: (args: { attemptId: string; payload: PaymentAttemptVoidPayload }) =>
      voidPaymentAttempt(args.attemptId, args.payload),
  });
}

export function mergeCheckoutResult(
  sale: SaleDetails | undefined,
  result: SaleCheckoutResult,
): SaleDetails {
  const base: SaleDetails = sale ?? {
    id: result.sale_id,
    tenant_id: result.tenant_id,
    branch_id: result.branch_id,
    register_id: result.register_id,
    shift_id: result.shift_id,
    sale_type: "sale",
    parent_sale_id: null,
    status: "completed",
    receipt_number: result.receipt_number,
    operation_id: result.operation_id,
    is_test: result.is_test,
    total_amount: result.total_amount,
    currency: result.currency,
    voided_at: null,
    voided_by_sale_id: null,
    cashier_user_id: result.cashier_user_id,
    created_at: result.created_at,
    completed_at: result.completed_at,
    items: [],
    payments: [],
  };
  return {
    ...base,
    status: "completed",
    operation_id: result.operation_id,
    receipt_number: result.receipt_number,
    completed_at: result.completed_at,
    total_amount: result.total_amount,
    items: result.items.map((item) => ({ ...item, sale_id: result.sale_id })),
    payments: result.payments.map((payment) => ({
      ...payment,
      sale_id: result.sale_id,
      operation_id: null,
    })),
  };
}

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
    mutationFn: (args: { registerId: string; operationId: string }) =>
      createSale(args.registerId, args.operationId),
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
    mutationFn: (args: {
      saleId: string;
      catalogId: string;
      qty: string;
      expiredSaleConfirmed?: boolean;
      operationId: string;
    }) =>
      addSaleItem(
        args.saleId,
        args.catalogId,
        args.qty,
        args.operationId,
        args.expiredSaleConfirmed,
      ),
    onSuccess: (_data, vars) =>
      qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId), exact: true }),
  });
}

export function useUpdateSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; itemId: string; qty: string; operationId: string }) =>
      updateSaleItem(args.saleId, args.itemId, args.qty, args.operationId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: posKeys.sale(vars.saleId) });
    },
  });
}

export function useDeleteSaleItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { saleId: string; itemId: string; operationId: string }) =>
      deleteSaleItem(args.saleId, args.itemId, args.operationId),
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
    mutationFn: (args: { saleId: string; expiredSaleConfirmed?: boolean }) =>
      completeSale(args.saleId, args.expiredSaleConfirmed),
    onSuccess: (data) => {
      qc.setQueryData<SaleDetails>(posKeys.sale(data.id), (sale) =>
        sale ? { ...sale, ...data } : sale,
      );
      // Completing affects inventory.
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}

export function useCheckoutSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SaleCheckoutPayload) => checkoutSale(payload),
    onSuccess: (result) => {
      qc.setQueryData<SaleDetails>(posKeys.sale(result.sale_id), (sale) =>
        mergeCheckoutResult(sale, result),
      );
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
