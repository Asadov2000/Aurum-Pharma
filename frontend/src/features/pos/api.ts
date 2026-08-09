import { api } from "@/lib/api";

import {
  type PaymentAddPayload,
  type Payment,
  type PaymentAttempt,
  type PaymentAttemptConfirmPayload,
  type PaymentAttemptCreatePayload,
  type PaymentAttemptVoidPayload,
  type PosFavorite,
  type PosFavoriteRecord,
  type PosCommandResult,
  type PrescriptionLog,
  type PrescriptionLogPayload,
  type ReceiptData,
  type Sale,
  type SaleCheckoutPayload,
  type SaleCheckoutResult,
  type SaleDetails,
  type SaleItem,
  type SaleItemAddedResponse,
  type SaleItemDeletedResponse,
  type Shift,
  type ShiftClosePayload,
  type ShiftOpenPayload,
  type ZReport,
} from "./types";

const POS_MONEY_WRITE_TIMEOUT_MS = 15_000;

// ---- electronic payment attempts ----

export async function createPaymentAttempt(
  payload: PaymentAttemptCreatePayload,
): Promise<PaymentAttempt> {
  const { data } = await api.post<PaymentAttempt>("/pos/payment-attempts", payload, {
    timeout: POS_MONEY_WRITE_TIMEOUT_MS,
  });
  return data;
}

export async function confirmPaymentAttempt(
  attemptId: string,
  payload: PaymentAttemptConfirmPayload = {},
): Promise<PaymentAttempt> {
  const { data } = await api.post<PaymentAttempt>(
    `/pos/payment-attempts/${attemptId}/confirm`,
    payload,
    { timeout: POS_MONEY_WRITE_TIMEOUT_MS },
  );
  return data;
}

export async function voidPaymentAttempt(
  attemptId: string,
  payload: PaymentAttemptVoidPayload,
): Promise<PaymentAttempt> {
  const { data } = await api.post<PaymentAttempt>(
    `/pos/payment-attempts/${attemptId}/void`,
    payload,
    { timeout: POS_MONEY_WRITE_TIMEOUT_MS },
  );
  return data;
}

// ---- cashier favorites ----

export async function getPosFavorites(branchId: string): Promise<PosFavorite[]> {
  const { data } = await api.get<PosFavorite[]>("/pos/favorites", {
    params: { branch_id: branchId },
  });
  return data;
}

export async function addPosFavorite(catalogId: string): Promise<PosFavoriteRecord> {
  const { data } = await api.post<PosFavoriteRecord>("/pos/favorites", {
    catalog_id: catalogId,
  });
  return data;
}

export async function removePosFavorite(catalogId: string): Promise<void> {
  await api.delete(`/pos/favorites/${catalogId}`);
}

// ---- Shifts ----

export async function openShift(payload: ShiftOpenPayload): Promise<Shift> {
  const { data } = await api.post<Shift>("/shifts/open", payload);
  return data;
}

export async function getCurrentShift(registerId: string): Promise<Shift | null> {
  const { data } = await api.get<Shift | null>("/shifts/current", {
    params: { register_id: registerId },
  });
  return data;
}

export async function closeShift(shiftId: string, payload: ShiftClosePayload): Promise<Shift> {
  const { data } = await api.post<Shift>(`/shifts/${shiftId}/close`, payload);
  return data;
}

export async function getZReport(shiftId: string): Promise<ZReport> {
  const { data } = await api.get<ZReport>(`/shifts/${shiftId}/z-report`);
  return data;
}

/** Z-report as an Excel workbook (closed shifts only) — authed blob. */
export async function getZReportXlsx(shiftId: string): Promise<Blob> {
  const { data } = await api.get<Blob>(`/shifts/${shiftId}/z-report.xlsx`, {
    responseType: "blob",
  });
  return data;
}

// ---- Sales ----

export async function createSale(registerId: string, operationId: string): Promise<Sale> {
  const { data } = await api.post<Sale>("/sales", {
    register_id: registerId,
    operation_id: operationId,
  });
  return data;
}

export async function getSale(id: string): Promise<SaleDetails> {
  const { data } = await api.get<SaleDetails>(`/sales/${id}`);
  return data;
}

export async function addSaleItem(
  saleId: string,
  catalogId: string,
  qty: string,
  operationId: string,
  expiredSaleConfirmed = false,
): Promise<SaleItemAddedResponse> {
  const { data } = await api.post<SaleItemAddedResponse>(`/sales/${saleId}/items`, {
    catalog_id: catalogId,
    qty,
    expired_sale_confirmed: expiredSaleConfirmed,
    operation_id: operationId,
  });
  return data;
}

export async function updateSaleItem(
  saleId: string,
  itemId: string,
  qty: string,
  operationId: string,
): Promise<SaleItem> {
  const { data } = await api.patch<SaleItem>(`/sales/${saleId}/items/${itemId}`, {
    qty,
    operation_id: operationId,
  });
  return data;
}

export async function deleteSaleItem(
  saleId: string,
  itemId: string,
  operationId: string,
): Promise<SaleItemDeletedResponse> {
  const { data } = await api.delete<SaleItemDeletedResponse>(`/sales/${saleId}/items/${itemId}`, {
    data: { operation_id: operationId },
  });
  return data;
}

export async function getPosCommandResult(operationId: string): Promise<PosCommandResult> {
  const { data } = await api.get<PosCommandResult>(`/pos/commands/${operationId}`);
  return data;
}

export async function addPayment(saleId: string, payload: PaymentAddPayload): Promise<Payment> {
  const { data } = await api.post<Payment>(`/sales/${saleId}/payments`, payload, {
    timeout: POS_MONEY_WRITE_TIMEOUT_MS,
  });
  return data;
}

export async function completeSale(saleId: string, expiredSaleConfirmed = false): Promise<Sale> {
  const { data } = await api.post<Sale>(
    `/sales/${saleId}/complete`,
    { expired_sale_confirmed: expiredSaleConfirmed },
    {
      timeout: POS_MONEY_WRITE_TIMEOUT_MS,
    },
  );
  return data;
}

export async function checkoutSale(payload: SaleCheckoutPayload): Promise<SaleCheckoutResult> {
  const { data } = await api.post<SaleCheckoutResult>("/sales/checkout", payload, {
    timeout: POS_MONEY_WRITE_TIMEOUT_MS,
  });
  return data;
}

export async function getCheckoutResult(operationId: string): Promise<SaleCheckoutResult> {
  const { data } = await api.get<SaleCheckoutResult>(`/sales/operations/${operationId}`);
  return data;
}

export async function addPrescription(
  saleId: string,
  payload: PrescriptionLogPayload,
): Promise<PrescriptionLog> {
  const { data } = await api.post<PrescriptionLog>(`/sales/${saleId}/prescription`, payload);
  return data;
}

// ---- receipt (print / PDF) ----

export async function getReceipt(saleId: string): Promise<ReceiptData> {
  const { data } = await api.get<ReceiptData>(`/sales/${saleId}/receipt`);
  return data;
}

/** Fetch the server-rendered PDF (authed) as a blob for download/preview. */
export async function getReceiptPdf(saleId: string): Promise<Blob> {
  const { data } = await api.get<Blob>(`/sales/${saleId}/receipt.pdf`, {
    responseType: "blob",
  });
  return data;
}
