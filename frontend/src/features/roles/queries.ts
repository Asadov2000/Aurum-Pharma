import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptOwnershipTransfer,
  archiveRole,
  cancelOwnershipTransfer,
  createAssignment,
  createOwnershipTransfer,
  createRole,
  listPermissions,
  listOwnershipTransfers,
  listRoles,
  listRoleVersions,
  listTemplates,
  listUsers,
  offboardUser,
  revokeAssignment,
  revokeUserSessions,
  reissueUserInvitation,
  suspendUser,
  updateRole,
  updateUser,
} from "./api";
import {
  type AssignmentCreatePayload,
  type OwnershipTransferCreatePayload,
  type RoleCreatePayload,
  type RoleArchivePayload,
  type RoleUpdatePayload,
  type UserSearchParams,
  type UserUpdatePayload,
} from "./types";

export const rolesKeys = {
  users: ["roles", "users"] as const,
  roles: ["roles", "roles"] as const,
  permissions: ["roles", "permissions"] as const,
  templates: ["roles", "templates"] as const,
  ownershipTransfers: ["roles", "ownership-transfers"] as const,
  versions: (roleId: string) => ["roles", "versions", roleId] as const,
};

export function useUsersQuery(params: UserSearchParams, enabled = true) {
  return useQuery({
    queryKey: [...rolesKeys.users, params] as const,
    queryFn: ({ signal }) => listUsers(params, signal),
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

export function useRoleVersionsQuery(roleId: string | null, enabled = true) {
  return useQuery({
    queryKey: rolesKeys.versions(roleId ?? "none"),
    queryFn: () => listRoleVersions(roleId ?? ""),
    enabled: enabled && roleId !== null,
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

export function useOwnershipTransfersQuery(enabled = true) {
  return useQuery({
    queryKey: rolesKeys.ownershipTransfers,
    queryFn: listOwnershipTransfers,
    enabled,
    staleTime: 15_000,
  });
}

export function useCreateOwnershipTransfer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: OwnershipTransferCreatePayload) => createOwnershipTransfer(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.ownershipTransfers });
    },
  });
}

export function useCancelOwnershipTransfer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (requestId: string) => cancelOwnershipTransfer(requestId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.ownershipTransfers });
    },
  });
}

export function useAcceptOwnershipTransfer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (requestId: string) => acceptOwnershipTransfer(requestId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rolesKeys.ownershipTransfers });
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
      void qc.invalidateQueries({ queryKey: rolesKeys.roles });
    },
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
    onSuccess: (_data, args) => {
      void qc.invalidateQueries({ queryKey: rolesKeys.roles });
      void qc.invalidateQueries({ queryKey: rolesKeys.versions(args.id) });
    },
  });
}

export function useArchiveRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: RoleArchivePayload }) =>
      archiveRole(args.id, args.payload),
    onSuccess: (_data, args) => {
      void qc.invalidateQueries({ queryKey: rolesKeys.roles });
      void qc.invalidateQueries({ queryKey: rolesKeys.users });
      void qc.invalidateQueries({ queryKey: rolesKeys.versions(args.id) });
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

export function useRevokeUserSessions() {
  return useMutation({ mutationFn: (id: string) => revokeUserSessions(id) });
}

export function useReissueUserInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, operationId }: { id: string; operationId: string }) =>
      reissueUserInvitation(id, operationId),
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
