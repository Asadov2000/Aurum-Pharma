import { api } from "@/lib/api";

import {
  type Assignment,
  type AssignmentCreatePayload,
  type InviteUserPayload,
  type Permission,
  type Role,
  type RoleCreatePayload,
  type RoleTemplate,
  type RoleUpdatePayload,
  type UserListResponse,
  type UserUpdatePayload,
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

export async function listUsers(page = 1, pageSize = 50): Promise<UserListResponse> {
  const { data } = await api.get<UserListResponse>("/users", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function inviteUser(payload: InviteUserPayload): Promise<Assignment> {
  const { data } = await api.post<Assignment>("/users/invite", payload);
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

export async function blockUser(userId: string): Promise<void> {
  await api.post(`/users/${userId}/block`);
}

export async function archiveUser(userId: string): Promise<void> {
  await api.delete(`/users/${userId}`);
}

export async function createAssignment(
  userId: string,
  payload: AssignmentCreatePayload,
): Promise<Assignment> {
  const { data } = await api.post<Assignment>(`/users/${userId}/assignments`, payload);
  return data;
}

export async function revokeAssignment(userId: string, assignmentId: string): Promise<void> {
  await api.delete(`/users/${userId}/assignments/${assignmentId}`);
}
