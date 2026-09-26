import { api } from "@/lib/api";
import {
  type CashPayment,
  type CashPaymentCreate,
  type CashPaymentDecision,
  type CashPaymentList,
  type CashReceipt,
} from "./cashTypes";

const platformPath = (tenantId: string) =>
  `/admin/billing/tenants/${encodeURIComponent(tenantId)}/cash-payments`;

export async function listTenantCashPayments(
  limit: number,
  offset: number,
  signal?: AbortSignal,
): Promise<CashPaymentList> {
  const { data } = await api.get<CashPaymentList>("/billing/cash-payments", {
    params: { limit, offset },
    signal,
  });
  return data;
}

export async function getTenantCashReceipt(id: string, signal?: AbortSignal): Promise<CashReceipt> {
  const { data } = await api.get<CashReceipt>(
    `/billing/cash-payments/${encodeURIComponent(id)}/receipt`,
    { signal },
  );
  return data;
}

export async function listPlatformCashPayments(
  tenantId: string,
  limit: number,
  offset: number,
  signal?: AbortSignal,
): Promise<CashPaymentList> {
  const { data } = await api.get<CashPaymentList>(platformPath(tenantId), {
    params: { limit, offset },
    signal,
  });
  return data;
}

export async function getPlatformCashReceipt(
  tenantId: string,
  id: string,
  signal?: AbortSignal,
): Promise<CashReceipt> {
  const { data } = await api.get<CashReceipt>(
    `${platformPath(tenantId)}/${encodeURIComponent(id)}/receipt`,
    { signal },
  );
  return data;
}

export async function createPlatformCashPayment(
  tenantId: string,
  payload: CashPaymentCreate,
): Promise<CashPayment> {
  const { data } = await api.post<CashPayment>(platformPath(tenantId), payload);
  return data;
}

export async function approvePlatformCashPayment(
  tenantId: string,
  id: string,
  payload: CashPaymentDecision,
): Promise<CashPayment> {
  const { data } = await api.post<CashPayment>(
    `${platformPath(tenantId)}/${encodeURIComponent(id)}/approve`,
    payload,
  );
  return data;
}

export async function rejectPlatformCashPayment(
  tenantId: string,
  id: string,
  payload: CashPaymentDecision & { reason: string },
): Promise<CashPayment> {
  const { data } = await api.post<CashPayment>(
    `${platformPath(tenantId)}/${encodeURIComponent(id)}/reject`,
    payload,
  );
  return data;
}
