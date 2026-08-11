import { api, withoutAuth } from "@/lib/api";

import {
  type PlatformStaffAccountFilters,
  type PlatformStaffAccountList,
  type PlatformStaffActivationPayload,
  type PlatformStaffInvitation,
  type PlatformStaffInvitationPayload,
} from "./types";

export async function listPlatformStaffAccounts(
  filters: PlatformStaffAccountFilters,
): Promise<PlatformStaffAccountList> {
  const { data } = await api.get<PlatformStaffAccountList>("/admin/platform-accounts", {
    params: filters,
  });
  return data;
}

export async function invitePlatformStaffAccount(
  payload: PlatformStaffInvitationPayload,
): Promise<PlatformStaffInvitation> {
  const { data } = await api.post<PlatformStaffInvitation>(
    "/admin/platform-accounts/invitations",
    payload,
  );
  return data;
}

export async function activatePlatformStaffAccount(
  payload: PlatformStaffActivationPayload,
): Promise<void> {
  await api.post("/auth/platform-activation", payload, withoutAuth({ timeout: 30_000 }));
}
