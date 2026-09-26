import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approvePlatformCashPayment,
  createPlatformCashPayment,
  getPlatformCashReceipt,
  getTenantCashReceipt,
  listPlatformCashPayments,
  listTenantCashPayments,
  rejectPlatformCashPayment,
} from "./cashApi";
import { type CashPaymentCreate, type CashPaymentDecision } from "./cashTypes";

export const CASH_PAGE_SIZE = 20;
export const cashKeys = {
  all: ["billing-cash"] as const,
  tenant: (page: number) => ["billing-cash", "tenant", page] as const,
  platform: (tenantId: string, page: number) =>
    ["billing-cash", "platform", tenantId, page] as const,
  receipt: (tenantId: string | undefined, id: string | null) =>
    ["billing-cash", "receipt", tenantId ?? "tenant", id] as const,
};

export function useTenantCashPayments(page: number) {
  return useQuery({
    queryKey: cashKeys.tenant(page),
    queryFn: ({ signal }) =>
      listTenantCashPayments(CASH_PAGE_SIZE, (page - 1) * CASH_PAGE_SIZE, signal),
    staleTime: 30_000,
  });
}

export function usePlatformCashPayments(tenantId: string, page: number) {
  return useQuery({
    queryKey: cashKeys.platform(tenantId, page),
    queryFn: ({ signal }) =>
      listPlatformCashPayments(tenantId, CASH_PAGE_SIZE, (page - 1) * CASH_PAGE_SIZE, signal),
    staleTime: 30_000,
  });
}

export function useCashReceipt(id: string | null, tenantId?: string) {
  return useQuery({
    queryKey: cashKeys.receipt(tenantId, id),
    queryFn: ({ signal }) => {
      if (!id) throw new Error("Receipt identifier is required");
      return tenantId
        ? getPlatformCashReceipt(tenantId, id, signal)
        : getTenantCashReceipt(id, signal);
    },
    enabled: id !== null,
    staleTime: 0,
  });
}

export function useCashPaymentCommands(tenantId: string) {
  const client = useQueryClient();
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: cashKeys.all }),
      client.invalidateQueries({ queryKey: ["platform-billing"] }),
      client.invalidateQueries({ queryKey: ["billing"] }),
    ]);
  };
  const create = useMutation({
    mutationFn: (payload: CashPaymentCreate) => createPlatformCashPayment(tenantId, payload),
    onSuccess: refresh,
    onError: () => client.invalidateQueries({ queryKey: cashKeys.all }),
  });
  const approve = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: CashPaymentDecision }) =>
      approvePlatformCashPayment(tenantId, id, payload),
    onSuccess: refresh,
    onError: () => client.invalidateQueries({ queryKey: cashKeys.all }),
  });
  const reject = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: CashPaymentDecision & { reason: string };
    }) => rejectPlatformCashPayment(tenantId, id, payload),
    onSuccess: refresh,
    onError: () => client.invalidateQueries({ queryKey: cashKeys.all }),
  });
  return { create, approve, reject };
}
