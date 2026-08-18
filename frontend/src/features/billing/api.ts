import { api } from "@/lib/api";

import {
  type Invoice,
  type InvoiceCreatePayload,
  type InvoiceWithPayments,
  type Payment,
  type PaymentCreatePayload,
  type Plan,
  type Subscription,
  type SubscriptionCreatePayload,
  type SubscriptionWithPlan,
  type TenantFinancialAccount,
  type TenantPaymentSubmissionCommandResult,
  type TenantPaymentSubmissionCreate,
  type TenantPaymentSubmissionList,
  type TenantPaymentSubmissionWithdraw,
} from "./types";

// ---- tenant-facing (read-only) ----

export async function listPlans(): Promise<Plan[]> {
  const { data } = await api.get<Plan[]>("/billing/plans");
  return data;
}

export async function getCurrentSubscription(): Promise<SubscriptionWithPlan | null> {
  const { data } = await api.get<SubscriptionWithPlan | null>("/billing/subscription");
  return data;
}

export async function listInvoices(): Promise<Invoice[]> {
  const { data } = await api.get<Invoice[]>("/billing/invoices");
  return data;
}

export async function getInvoice(id: string): Promise<InvoiceWithPayments> {
  const { data } = await api.get<InvoiceWithPayments>(`/billing/invoices/${id}`);
  return data;
}

export async function getFinancialAccount(signal?: AbortSignal): Promise<TenantFinancialAccount> {
  const { data } = await api.get<TenantFinancialAccount>("/billing/financial-account", { signal });
  return data;
}

export async function listPaymentSubmissions(
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<TenantPaymentSubmissionList> {
  const { data } = await api.get<TenantPaymentSubmissionList>("/billing/payment-submissions", {
    params: { page, page_size: pageSize },
    signal,
  });
  return data;
}

export async function createPaymentSubmission(
  payload: TenantPaymentSubmissionCreate,
): Promise<TenantPaymentSubmissionCommandResult> {
  const { data } = await api.post<TenantPaymentSubmissionCommandResult>(
    "/billing/payment-submissions",
    payload,
  );
  return data;
}

export async function withdrawPaymentSubmission(
  submissionId: string,
  payload: TenantPaymentSubmissionWithdraw,
): Promise<TenantPaymentSubmissionCommandResult> {
  const { data } = await api.post<TenantPaymentSubmissionCommandResult>(
    `/billing/payment-submissions/${submissionId}/withdraw`,
    payload,
  );
  return data;
}

// ---- admin (support-only writes) ----

export async function createSubscription(
  tenantId: string,
  payload: SubscriptionCreatePayload,
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(`/admin/tenants/${tenantId}/subscription`, payload);
  return data;
}

export async function createInvoice(
  tenantId: string,
  payload: InvoiceCreatePayload,
): Promise<Invoice> {
  const { data } = await api.post<Invoice>(`/admin/tenants/${tenantId}/invoices`, payload);
  return data;
}

export async function recordPayment(
  tenantId: string,
  invoiceId: string,
  payload: PaymentCreatePayload,
): Promise<Payment> {
  const { data } = await api.post<Payment>(
    `/admin/tenants/${tenantId}/invoices/${invoiceId}/payments`,
    payload,
  );
  return data;
}
