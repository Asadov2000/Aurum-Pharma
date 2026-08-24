import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addBarcode,
  confirmImport,
  createCatalogItem,
  deleteBarcode,
  deleteCatalogItem,
  getCatalogItem,
  getImportJob,
  listCatalog,
  previewImport,
  rollbackImport,
  restoreCatalogItem,
  updateCatalogItem,
  uploadImport,
} from "./api";
import {
  type BarcodeCreatePayload,
  type CatalogItemCreatePayload,
  type CatalogItemUpdatePayload,
  type CatalogSearchParams,
  type DuplicateStrategy,
} from "./types";

export const catalogKeys = {
  list: (params: CatalogSearchParams) => ["catalog", "list", params] as const,
  item: (id: string) => ["catalog", "item", id] as const,
  import: (id: string) => ["catalog", "import", id] as const,
};

export function useCatalogQuery(params: CatalogSearchParams, enabled = true) {
  return useQuery({
    queryKey: catalogKeys.list(params),
    queryFn: () => listCatalog(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCatalogItemQuery(id: string | null) {
  return useQuery({
    queryKey: catalogKeys.item(id ?? ""),
    queryFn: () => getCatalogItem(id as string),
    enabled: id !== null,
  });
}

export function useCreateCatalogItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CatalogItemCreatePayload) => createCatalogItem(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useUpdateCatalogItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: CatalogItemUpdatePayload }) =>
      updateCatalogItem(args.id, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: catalogKeys.item(vars.id) });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useDeleteCatalogItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCatalogItem(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useRestoreCatalogItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => restoreCatalogItem(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: catalogKeys.item(id) });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useAddBarcode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { itemId: string; payload: BarcodeCreatePayload }) =>
      addBarcode(args.itemId, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: catalogKeys.item(vars.itemId) });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useDeleteBarcode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { itemId: string; barcodeId: string }) =>
      deleteBarcode(args.itemId, args.barcodeId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: catalogKeys.item(vars.itemId) });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

// ---- import ----

export function useImportJobQuery(jobId: string | null) {
  return useQuery({
    queryKey: catalogKeys.import(jobId ?? ""),
    queryFn: () => getImportJob(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.status === "importing" ? 2000 : false),
  });
}

export function useUploadImport() {
  return useMutation({
    mutationFn: (file: File) => uploadImport(file),
  });
}

export function usePreviewImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => previewImport(jobId),
    onSuccess: (data) => {
      qc.setQueryData(catalogKeys.import(data.id), data);
    },
  });
}

export function useConfirmImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { jobId: string; strategy: DuplicateStrategy }) =>
      confirmImport(args.jobId, args.strategy),
    onSuccess: (data) => {
      qc.setQueryData(catalogKeys.import(data.id), data);
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}

export function useRollbackImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => rollbackImport(jobId),
    onSuccess: (data) => {
      qc.setQueryData(catalogKeys.import(data.id), data);
      void qc.invalidateQueries({ queryKey: ["catalog", "list"] });
      void qc.invalidateQueries({ queryKey: ["catalog", "summary"] });
    },
  });
}
