import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getBatch,
  listBatches,
  listMovements,
  writeOff,
} from "./api";
import { type BatchSearchParams, type WriteOffCreatePayload } from "./types";

export const inventoryKeys = {
  list: (params: BatchSearchParams) => ["inventory", "batches", params] as const,
  batch: (id: string) => ["inventory", "batch", id] as const,
  movements: (id: string) => ["inventory", "movements", id] as const,
};

export function useBatchesQuery(params: BatchSearchParams, enabled = true) {
  return useQuery({
    queryKey: inventoryKeys.list(params),
    queryFn: () => listBatches(params),
    enabled,
  });
}

export function useBatchQuery(id: string | null) {
  return useQuery({
    queryKey: inventoryKeys.batch(id ?? ""),
    queryFn: () => getBatch(id as string),
    enabled: id !== null,
  });
}

export function useMovementsQuery(id: string | null) {
  return useQuery({
    queryKey: inventoryKeys.movements(id ?? ""),
    queryFn: () => listMovements(id as string),
    enabled: id !== null,
  });
}

export function useWriteOff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { batchId: string; payload: WriteOffCreatePayload }) =>
      writeOff(args.batchId, args.payload),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
      void qc.invalidateQueries({ queryKey: inventoryKeys.batch(vars.batchId) });
      void qc.invalidateQueries({ queryKey: inventoryKeys.movements(vars.batchId) });
    },
  });
}
