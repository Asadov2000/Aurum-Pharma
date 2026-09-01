import { api } from "@/lib/api";

import {
  type Assignment,
  type AssignmentBatchReplacePayload,
  type AssignmentCreatePayload,
  type AssignmentHistoryEvent,
  type OwnershipTransfer,
  type OwnershipTransferActionResponse,
  type OwnershipTransferCreatePayload,
  type Permission,
  type Role,
  type RoleArchivePayload,
  type RoleArchiveResponse,
  type RoleCreatePayload,
  type RoleTemplate,
  type RoleUpdatePayload,
  type RoleVersion,
  type UserListResponse,
  type UserSearchParams,
  type UserUpdatePayload,
  type InvitationRead,
  type InviteEmployeePayload,
} from "./types";

export async function listPermissions(): Promise<Permission[]> {
  const { data } = await api.get<Permission[]>("/permissions");
  return data;
}

export async function listRoles(): Promise<Role[]> {
  const { data } = await api.get<Role[]>("/roles");
  return data;
}

export async function listTemplates(): Promise<RoleTemplate[]> {
  const { data } = await api.get<RoleTemplate[]>("/templates");
  return data;
}

export async function createRole(payload: RoleCreatePayload): Promise<Role> {
  const { data } = await api.post<Role>("/roles", payload);
  return data;
}

export async function updateRole(roleId: string, payload: RoleUpdatePayload): Promise<Role> {
  const { data } = await api.patch<Role>(`/roles/${roleId}`, payload);
  return data;
}

export async function listRoleVersions(roleId: string): Promise<RoleVersion[]> {
  const { data } = await api.get<RoleVersion[]>(`/roles/${roleId}/versions`);
  return data;
}

export async function archiveRole(
  roleId: string,
  payload: RoleArchivePayload,
): Promise<RoleArchiveResponse> {
  const { data } = await api.post<RoleArchiveResponse>(`/roles/${roleId}/archive`, payload);
  return data;
}

export async function listUsers(
  params: UserSearchParams = {},
  signal?: AbortSignal,
): Promise<UserListResponse> {
  const { data } = await api.post<UserListResponse>(
    "/users/search",
    {
      q: params.q || undefined,
      status: params.status || undefined,
      role_id: params.role_id || undefined,
      branch_id: params.branch_id || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
    { signal },
  );
  return data;
}

export async function updateUser(
  userId: string,
  payload: UserUpdatePayload,
): Promise<{ id: string; full_name: string; phone: string | null }> {
  const { data } = await api.patch<{ id: string; full_name: string; phone: string | null }>(
    `/users/${userId}`,
    payload,
  );
  return data;
}

export async function suspendUser(userId: string): Promise<void> {
  await api.post(`/users/${userId}/block`);
}

export async function offboardUser(userId: string): Promise<void> {
  await api.delete(`/users/${userId}`);
}

export async function revokeUserSessions(
  userId: string,
): Promise<{ status: "ok"; revoked_count: number }> {
  const { data } = await api.post<{ status: "ok"; revoked_count: number }>(
    `/users/${userId}/sessions/revoke`,
  );
  return data;
}

export async function reissueUserInvitation(
  userId: string,
  operationId: string,
): Promise<InvitationRead> {
  const { data } = await api.post<InvitationRead>(`/users/${userId}/invitation/reissue`, {
    operation_id: operationId,
  });
  return data;
}

export async function inviteEmployee(payload: InviteEmployeePayload): Promise<Assignment> {
  const { data } = await api.post<Assignment>("/users/invite", payload);
  return data;
}

export async function createAssignment(
  userId: string,
  payload: AssignmentCreatePayload,
): Promise<Assignment> {
  const { data } = await api.post<Assignment>(`/users/${userId}/assignments`, payload);
  return data;
}

export async function replaceAssignments(
  userId: string,
  payload: AssignmentBatchReplacePayload,
): Promise<Assignment[]> {
  const { data } = await api.put<Assignment[]>(`/users/${userId}/assignments`, payload);
  return data;
}

export async function revokeAssignment(userId: string, assignmentId: string): Promise<void> {
  await api.delete(`/users/${userId}/assignments/${assignmentId}`);
}

export async function listAssignmentHistory(userId: string): Promise<AssignmentHistoryEvent[]> {
  const { data } = await api.get<AssignmentHistoryEvent[]>(`/users/${userId}/access-history`);
  return data;
}

export async function listOwnershipTransfers(): Promise<OwnershipTransfer[]> {
  const { data } = await api.get<{ items: OwnershipTransfer[] }>("/ownership-transfers");
  return data.items;
}

export async function createOwnershipTransfer(
  payload: OwnershipTransferCreatePayload,
): Promise<OwnershipTransferActionResponse> {
  const { data } = await api.post<OwnershipTransferActionResponse>("/ownership-transfers", payload);
  return data;
}

export async function cancelOwnershipTransfer(
  requestId: string,
): Promise<OwnershipTransferActionResponse> {
  const { data } = await api.post<OwnershipTransferActionResponse>(
    `/ownership-transfers/${requestId}/cancel`,
  );
  return data;
}

export async function acceptOwnershipTransfer(
  requestId: string,
): Promise<OwnershipTransferActionResponse> {
  const { data } = await api.post<OwnershipTransferActionResponse>(
    `/ownership-transfers/${requestId}/accept`,
  );
  return data;
}
