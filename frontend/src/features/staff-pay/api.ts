import { api } from "@/lib/api";

import {
  type EntryCreate,
  type PeriodCreate,
  type PeriodUpdate,
  type StaffPayEmployee,
  type StaffPayEntry,
  type StaffPayList,
  type StaffPayPeriod,
  type StaffPayRevision,
} from "./types";

export async function listStaffPay(
  own: boolean,
  employeeId: string | null,
  page: number,
  signal?: AbortSignal,
): Promise<StaffPayList<StaffPayPeriod>> {
  const { data } = await api.get<StaffPayList<StaffPayPeriod>>(
    own ? "/staff-pay/mine" : "/staff-pay",
    {
      params: { page, page_size: 10, ...(own ? {} : { employee_id: employeeId }) },
      signal,
    },
  );
  return data;
}

export async function searchStaffPayEmployees(
  q: string,
  page: number,
  signal?: AbortSignal,
): Promise<StaffPayList<StaffPayEmployee>> {
  const { data } = await api.post<StaffPayList<StaffPayEmployee>>(
    "/staff-pay/employees/search",
    { q, page, page_size: 25 },
    { signal },
  );
  return data;
}

export async function listStaffPayEntries(
  own: boolean,
  periodId: string,
  page: number,
  signal?: AbortSignal,
): Promise<StaffPayList<StaffPayEntry>> {
  const { data } = await api.get<StaffPayList<StaffPayEntry>>(
    `/staff-pay/${own ? "mine/" : ""}${periodId}/entries`,
    { params: { page, page_size: 25 }, signal },
  );
  return data;
}

export async function listStaffPayRevisions(
  own: boolean,
  periodId: string,
  page: number,
  signal?: AbortSignal,
): Promise<StaffPayList<StaffPayRevision>> {
  const { data } = await api.get<StaffPayList<StaffPayRevision>>(
    `/staff-pay/${own ? "mine/" : ""}${periodId}/revisions`,
    { params: { page, page_size: 25 }, signal },
  );
  return data;
}

export async function createStaffPayPeriod(payload: PeriodCreate): Promise<StaffPayPeriod> {
  return (await api.post<StaffPayPeriod>("/staff-pay", payload)).data;
}
export async function updateStaffPayPeriod(
  id: string,
  payload: PeriodUpdate,
): Promise<StaffPayPeriod> {
  return (await api.patch<StaffPayPeriod>(`/staff-pay/${id}`, payload)).data;
}
export async function createStaffPayEntry(
  id: string,
  payload: EntryCreate,
): Promise<StaffPayEntry> {
  return (await api.post<StaffPayEntry>(`/staff-pay/${id}/entries`, payload)).data;
}
export async function reverseStaffPayEntry(
  periodId: string,
  entryId: string,
  payload: { reason: string; operation_id: string },
): Promise<StaffPayEntry> {
  return (
    await api.post<StaffPayEntry>(`/staff-pay/${periodId}/entries/${entryId}/reverse`, payload)
  ).data;
}
