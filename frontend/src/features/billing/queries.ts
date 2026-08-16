import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createInvoice,
  createSubscription,
  getCurrentSubscription,
  getFinancialAccount,
  getInvoice,
  listInvoices,
  listPlans,
  recordPayment,
} from "./api";
import {
  type InvoiceCreatePayload,
  type PaymentCreatePayload,
  type SubscriptionCreatePayload,
} from "./types";

export const billingKeys = {
  plans: ["billing", "plans"] as const,
  subscription: ["billing", "subscription"] as const,
  invoices: ["billing", "invoices"] as const,
  invoice: (id: string) => ["billing", "invoice", id] as const,
  financialAccount: ["billing", "financial-account"] as const,
};

export function usePlansQuery(enabled = true) {
  return useQuery({
    queryKey: billingKeys.plans,
    queryFn: listPlans,
    enabled,
  });
}

export function useSubscriptionQuery(enabled = true) {
  return useQuery({
    queryKey: billingKeys.subscription,
    queryFn: getCurrentSubscription,
    enabled,
  });
}

export function useInvoicesQuery(enabled = true) {
  return useQuery({
    queryKey: billingKeys.invoices,
    queryFn: listInvoices,
    enabled,
  });
}

export function useInvoiceQuery(id: string | null) {
  return useQuery({
    queryKey: billingKeys.invoice(id ?? ""),
    queryFn: () => getInvoice(id as string),
    enabled: id !== null && id !== "",
  });
}

export function useFinancialAccountQuery() {
  return useQuery({
    queryKey: billingKeys.financialAccount,
    queryFn: ({ signal }) => getFinancialAccount(signal),
    staleTime: 15_000,
  });
}

export function useCreateSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tenantId: string; payload: SubscriptionCreatePayload }) =>
      createSubscription(args.tenantId, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: billingKeys.subscription });
    },
  });
}

export function useCreateInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tenantId: string; payload: InvoiceCreatePayload }) =>
      createInvoice(args.tenantId, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: billingKeys.invoices });
    },
  });
}

export function useRecordPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tenantId: string; invoiceId: string; payload: PaymentCreatePayload }) =>
      recordPayment(args.tenantId, args.invoiceId, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: billingKeys.invoices });
      void qc.invalidateQueries({ queryKey: billingKeys.invoice(vars.invoiceId) });
    },
  });
}
