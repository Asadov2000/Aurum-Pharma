import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveUser,
  blockUser,
  createAssignment,
  inviteUser,
  listPermissions,
  listRoles,
  listUsers,
  revokeAssignment,
  updateUser,
} from "./api";
import {
  type AssignmentCreatePayload,
  type InviteUserPayload,
  type UserUpdatePayload,
} from "./types";

export const rolesKeys = {
  users: ["roles", "users"] as const,
  roles: ["roles", "roles"] as const,
  permissions: ["roles", "permissions"] as const,
};

export function useUsersQuery(enabled = true) {
  return useQuery({
    queryKey: rolesKeys.users,
    queryFn: listUsers,
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

export function useInviteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: InviteUserPayload) => inviteUser(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
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

export function useBlockUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => blockUser(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
    },
  });
}

export function useArchiveUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => archiveUser(id),
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
