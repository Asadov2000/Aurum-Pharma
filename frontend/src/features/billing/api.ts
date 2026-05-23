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

// ---- admin (support-only writes) ----

export async function createSubscription(
  tenantId: string,
  payload: SubscriptionCreatePayload,
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/admin/tenants/${tenantId}/subscription`,
    payload,
  );
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
