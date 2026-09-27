import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { activeTenantId } from "@/features/auth/tenantContext";
import { useAuthStore } from "@/stores/auth";

import * as staffPay from "./api";
import { type EntryCreate, type PeriodCreate, type PeriodUpdate } from "./types";

export function useStaffPayAccess() {
  const user = useAuthStore((state) => state.user);
  const authenticated = useAuthStore((state) => state.accessToken !== null);
  const ordinary =
    authenticated &&
    !!user &&
    !!activeTenantId(user) &&
    !user.is_developer &&
    !user.is_administrator &&
    !user.support_access;
  const manage =
    ordinary &&
    (user?.is_tenant_owner === true || user?.permissions.includes("staff_pay.manage") === true);
  return {
    ordinary,
    manage,
    view: manage || (ordinary && user?.permissions.includes("staff_pay.view") === true),
    scope: [user?.id ?? null, activeTenantId(user), user?.support_access?.id ?? null],
  };
}

export function useStaffPayPeriods(
  own: boolean,
  employeeId: string | null,
  page: number,
  visible = true,
) {
  const access = useStaffPayAccess();
  return useQuery({
    queryKey: ["staff-pay", ...access.scope, own ? "mine" : "management", employeeId, page],
    queryFn: ({ signal }) => staffPay.listStaffPay(own, employeeId, page, signal),
    enabled: visible && (own ? access.ordinary : access.view && employeeId !== null),
    staleTime: 30_000,
    refetchOnMount: "always",
  });
}

export function useStaffPayEmployees(q: string, page: number) {
  const access = useStaffPayAccess();
  return useQuery({
    queryKey: ["staff-pay", ...access.scope, "employees", q, page],
    queryFn: ({ signal }) => staffPay.searchStaffPayEmployees(q, page, signal),
    enabled: access.view,
    staleTime: 30_000,
  });
}

export function useStaffPayEntries(own: boolean, periodId: string, page: number) {
  const access = useStaffPayAccess();
  return useQuery({
    queryKey: [
      "staff-pay",
      ...access.scope,
      own ? "mine" : "management",
      "entries",
      periodId,
      page,
    ],
    queryFn: ({ signal }) => staffPay.listStaffPayEntries(own, periodId, page, signal),
    enabled: own ? access.ordinary : access.view,
    staleTime: 0,
  });
}

export function useStaffPayRevisions(own: boolean, periodId: string, page: number) {
  const access = useStaffPayAccess();
  return useQuery({
    queryKey: [
      "staff-pay",
      ...access.scope,
      own ? "mine" : "management",
      "revisions",
      periodId,
      page,
    ],
    queryFn: ({ signal }) => staffPay.listStaffPayRevisions(own, periodId, page, signal),
    enabled: own ? access.ordinary : access.view,
    staleTime: 0,
  });
}

export function useStaffPayCommands() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["staff-pay"] });
  return {
    create: useMutation({
      mutationFn: (payload: PeriodCreate) => staffPay.createStaffPayPeriod(payload),
      onSettled: refresh,
    }),
    update: useMutation({
      mutationFn: ({ id, payload }: { id: string; payload: PeriodUpdate }) =>
        staffPay.updateStaffPayPeriod(id, payload),
      onSettled: refresh,
    }),
    entry: useMutation({
      mutationFn: ({ id, payload }: { id: string; payload: EntryCreate }) =>
        staffPay.createStaffPayEntry(id, payload),
      onSettled: refresh,
    }),
    reverse: useMutation({
      mutationFn: ({
        periodId,
        entryId,
        payload,
      }: {
        periodId: string;
        entryId: string;
        payload: { reason: string; operation_id: string };
      }) => staffPay.reverseStaffPayEntry(periodId, entryId, payload),
      onSettled: refresh,
    }),
  };
}
