import { api } from "@/lib/api";
import { type Sale, type SaleDetails } from "@/features/pos/types";

import {
  type RefundAttempt,
  type RefundAttemptConfirmation,
  type RefundLine,
  type RefundPayload,
  type SaleList,
  type SaleSearchParams,
} from "./types";

const MONEY_OPERATION_TIMEOUT_MS = 15_000;

export async function listSales(params: SaleSearchParams): Promise<SaleList> {
  const { data } = await api.get<SaleList>("/sales", {
    params: {
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
      receipt_number: params.receipt_number || undefined,
      branch_id: params.branch_id || undefined,
      register_id: params.register_id || undefined,
      cashier_id: params.cashier_id || undefined,
      has_refund: params.has_refund,
      min_total: params.min_total || undefined,
      max_total: params.max_total || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  });
  return data;
}

export async function getSaleDetails(saleId: string): Promise<SaleDetails> {
  const { data } = await api.get<SaleDetails>(`/sales/${saleId}`);
  return data;
}

export async function refundSale(parentSaleId: string, payload: RefundPayload): Promise<Sale> {
  const { data } = await api.post<Sale>(`/sales/${parentSaleId}/refund`, payload, {
    timeout: MONEY_OPERATION_TIMEOUT_MS,
  });
  return data;
}

export async function getRefundResult(operationId: string): Promise<Sale> {
  const { data } = await api.get<Sale>(`/sales/refund-operations/${operationId}`, {
    timeout: MONEY_OPERATION_TIMEOUT_MS,
  });
  return data;
}

export async function createRefundAttempt(
  parentSaleId: string,
  operationId: string,
  items: RefundLine[],
): Promise<RefundAttempt> {
  const { data } = await api.post<RefundAttempt>(
    `/sales/${parentSaleId}/refund-attempts`,
    { operation_id: operationId, items },
    { timeout: MONEY_OPERATION_TIMEOUT_MS },
  );
  return data;
}

export async function getRefundAttempt(attemptId: string): Promise<RefundAttempt> {
  const { data } = await api.get<RefundAttempt>(`/pos/refund-attempts/${attemptId}`, {
    timeout: MONEY_OPERATION_TIMEOUT_MS,
  });
  return data;
}

export async function confirmRefundAttempt(
  attemptId: string,
  confirmations: RefundAttemptConfirmation[],
): Promise<RefundAttempt> {
  const { data } = await api.post<RefundAttempt>(
    `/pos/refund-attempts/${attemptId}/confirm`,
    { confirmations },
    { timeout: MONEY_OPERATION_TIMEOUT_MS },
  );
  return data;
}

export async function voidRefundAttempt(attemptId: string): Promise<RefundAttempt> {
  const { data } = await api.post<RefundAttempt>(
    `/pos/refund-attempts/${attemptId}/void`,
    { reason: "cashier_cancelled", operator_note: null },
    { timeout: MONEY_OPERATION_TIMEOUT_MS },
  );
  return data;
}
