import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptIncoming,
  addIncomingItem,
  createIncoming,
  deleteIncomingItem,
  getIncoming,
  listIncoming,
  listIncomingDraftRows,
  rejectIncoming,
  updateIncoming,
  updateIncomingItem,
} from "./api";
import {
  type IncomingDocumentCreatePayload,
  type IncomingAcceptPayload,
  type IncomingDocumentUpdatePayload,
  type IncomingDocumentWithItems,
  type IncomingItemCreatePayload,
  type IncomingItemUpdatePayload,
  type IncomingSearchParams,
} from "./types";

export const incomingKeys = {
  list: (params: IncomingSearchParams) => ["incoming", "list", params] as const,
  doc: (id: string) => ["incoming", "doc", id] as const,
  drafts: (id: string) => ["incoming", "draft-rows", id] as const,
};

export function useIncomingDraftRowsQuery(id: string, enabled: boolean) {
  return useQuery({
    queryKey: incomingKeys.drafts(id),
    queryFn: ({ signal }) => listIncomingDraftRows(id, signal),
    enabled,
    staleTime: 0,
    refetchOnMount: "always",
  });
}

export function useIncomingListQuery(params: IncomingSearchParams, enabled = true) {
  return useQuery({
    queryKey: incomingKeys.list(params),
    queryFn: ({ signal }) => listIncoming(params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useIncomingDocQuery(id: string | null) {
  return useQuery({
    queryKey: incomingKeys.doc(id ?? ""),
    queryFn: ({ signal }) => getIncoming(id as string, signal),
    enabled: id !== null,
  });
}

export function useCreateIncoming() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: IncomingDocumentCreatePayload) => createIncoming(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
    },
  });
}

export function useUpdateIncoming() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: IncomingDocumentUpdatePayload }) =>
      updateIncoming(args.id, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(vars.id) });
    },
  });
}

export function useAddIncomingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { documentId: string; payload: IncomingItemCreatePayload }) =>
      addIncomingItem(args.documentId, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(vars.documentId) });
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
    },
  });
}

export function useUpdateIncomingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      documentId: string;
      itemId: string;
      payload: IncomingItemUpdatePayload;
    }) => updateIncomingItem(args.documentId, args.itemId, args.payload),
    onSuccess: async (data, vars) => {
      await qc.cancelQueries({ queryKey: incomingKeys.doc(vars.documentId) });
      qc.setQueryData<IncomingDocumentWithItems>(incomingKeys.doc(vars.documentId), (current) =>
        current
          ? { ...current, items: current.items.map((item) => (item.id === data.id ? data : item)) }
          : undefined,
      );
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(vars.documentId) });
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
    },
  });
}

export function useDeleteIncomingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { documentId: string; itemId: string }) =>
      deleteIncomingItem(args.documentId, args.itemId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(vars.documentId) });
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
    },
  });
}

export function useAcceptIncoming() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: string | { id: string; payload: IncomingAcceptPayload }) =>
      typeof args === "string" ? acceptIncoming(args) : acceptIncoming(args.id, args.payload),
    onSuccess: (_data, args) => {
      const id = typeof args === "string" ? args : args.id;
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(id) });
      // Accepting creates batches → refresh inventory.
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}

export function useRejectIncoming() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => rejectIncoming(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ["incoming", "list"] });
      void qc.invalidateQueries({ queryKey: incomingKeys.doc(id) });
    },
  });
}
