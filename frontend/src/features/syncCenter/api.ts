import { api } from "@/lib/api";

import {
  type SyncCredentialRotationSecret,
  type SyncCredentialRotationStartPayload,
  type SyncCredentialRotationTransition,
  type SyncMonitoringFilters,
  type SyncMonitoringOverview,
  type SyncNodeActionPayload,
  type SyncNodeLifecycleResult,
} from "./types";

export async function getSyncMonitoringOverview(
  filters: SyncMonitoringFilters,
  signal?: AbortSignal,
): Promise<SyncMonitoringOverview> {
  const { data } = await api.get<SyncMonitoringOverview>("/admin/sync/overview", {
    params: filters,
    signal,
  });
  return data;
}

export async function startSyncCredentialRotation(
  nodeId: string,
  payload: SyncCredentialRotationStartPayload,
): Promise<SyncCredentialRotationSecret> {
  const { data } = await api.post<SyncCredentialRotationSecret>(
    `/admin/sync/nodes/${encodeURIComponent(nodeId)}/credential-rotations`,
    payload,
  );
  return data;
}

export async function transitionSyncCredentialRotation(
  rotationId: string,
  action: "complete" | "cancel",
  payload: SyncNodeActionPayload,
): Promise<SyncCredentialRotationTransition> {
  const { data } = await api.post<SyncCredentialRotationTransition>(
    `/admin/sync/credential-rotations/${encodeURIComponent(rotationId)}/${action}`,
    payload,
  );
  return data;
}

export async function revokeSyncNode(
  nodeId: string,
  payload: SyncNodeActionPayload,
): Promise<SyncNodeLifecycleResult> {
  const { data } = await api.post<SyncNodeLifecycleResult>(
    `/admin/sync/nodes/${encodeURIComponent(nodeId)}/revoke`,
    payload,
  );
  return data;
}
