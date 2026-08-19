import { api } from "@/lib/api";

import {
  type TenantFinancialAccount,
  type TenantPaymentSubmissionCommandResult,
  type TenantPaymentSubmissionCreate,
  type TenantPaymentSubmissionList,
  type TenantPaymentSubmissionWithdraw,
} from "./types";

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
