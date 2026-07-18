import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createAssignment,
  createRole,
  listPermissions,
  listRoles,
  listTemplates,
  listUsers,
  offboardUser,
  revokeAssignment,
  suspendUser,
  updateRole,
  updateUser,
} from "./api";
import {
  type AssignmentCreatePayload,
  type RoleCreatePayload,
  type RoleUpdatePayload,
  type UserUpdatePayload,
} from "./types";

export const rolesKeys = {
  users: ["roles", "users"] as const,
  roles: ["roles", "roles"] as const,
  permissions: ["roles", "permissions"] as const,
  templates: ["roles", "templates"] as const,
};

export function useUsersQuery(enabled = true, page = 1, pageSize = 50) {
  return useQuery({
    queryKey: [...rolesKeys.users, page, pageSize] as const,
    queryFn: () => listUsers(page, pageSize),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useRolesQuery(enabled = true) {
  return useQuery({
    queryKey: rolesKeys.roles,
    queryFn: listRoles,
    enabled,
  });
}

export function usePermissionsQuery(enabled = true) {
  return useQuery({
    queryKey: rolesKeys.permissions,
    queryFn: listPermissions,
    enabled,
  });
}

export function useTemplatesQuery(enabled = true) {
  return useQuery({
    queryKey: rolesKeys.templates,
    queryFn: listTemplates,
    enabled,
  });
}

export function useCreateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RoleCreatePayload) => createRole(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.roles });
    },
  });
}

export function useUpdateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: RoleUpdatePayload }) =>
      updateRole(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.roles });
    },
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: UserUpdatePayload }) =>
      updateUser(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}

export function useSuspendUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => suspendUser(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}

export function useOffboardUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => offboardUser(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}

export function useCreateAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { userId: string; payload: AssignmentCreatePayload }) =>
      createAssignment(args.userId, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}

export function useRevokeAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { userId: string; assignmentId: string }) =>
      revokeAssignment(args.userId, args.assignmentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}
