import { api } from "@/lib/api";

import {
  type PlatformAccessActionPayload,
  type PlatformAccessGrant,
  type PlatformAccessGrantFilters,
  type PlatformAccessGrantList,
} from "./types";

export async function listPlatformAccessGrants(
  filters: PlatformAccessGrantFilters,
): Promise<PlatformAccessGrant[]> {
  const { data } = await api.get<PlatformAccessGrantList>("/admin/platform-access/grants", {
    params: filters,
  });
  return data.items;
}

export async function approvePlatformAccessGrant(
  grantId: string,
  payload: PlatformAccessActionPayload,
): Promise<PlatformAccessGrant> {
  const { data } = await api.post<PlatformAccessGrant>(
    `/admin/platform-access/grants/${grantId}/approve`,
    payload,
  );
  return data;
}

export async function revokePlatformAccessGrant(
  grantId: string,
  payload: PlatformAccessActionPayload,
): Promise<PlatformAccessGrant> {
  const { data } = await api.post<PlatformAccessGrant>(
    `/admin/platform-access/grants/${grantId}/revoke`,
    payload,
  );
  return data;
}
