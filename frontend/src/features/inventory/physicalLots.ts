import { z } from "zod";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";

const integer = z.string().regex(/^(0|[1-9]\d*)$/);
const packaging = z
  .object({
    version: z.literal(2),
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    units_per_pack: z.number().int().positive(),
    units_per_blister: z.number().int().positive().nullable(),
    dispense_step: z.number().int().positive(),
  })
  .strict();
export const physicalLotSchema = z
  .object({
    lot_id: z.string().uuid(),
    branch_id: z.string().uuid(),
    catalog_id: z.string().uuid(),
    catalog_name: z.string().nullable(),
    expires_at: z.string(),
    packaging,
    closed_packs: integer,
    opened_units: integer,
    base_units: integer,
    is_blocked: z.boolean(),
    blocking_revision: integer,
  })
  .strict();
const listSchema = z
  .object({
    items: z.array(physicalLotSchema),
    total: z.number().int().nonnegative(),
    page: z.number().int().positive(),
    page_size: z.number().int().positive(),
  })
  .strict();
const blockingSchema = z
  .object({
    lot_id: z.string().uuid(),
    branch_id: z.string().uuid(),
    is_blocked: z.boolean(),
    blocking_revision: integer,
  })
  .strict();
export type PhysicalLot = z.infer<typeof physicalLotSchema>;
export interface PhysicalLotFilters {
  branch_id: string;
  q?: string;
  is_blocked?: boolean;
  show_empty: boolean;
  page: number;
  page_size: number;
}
export async function listPhysicalLots(params: PhysicalLotFilters, signal?: AbortSignal) {
  const { data } = await api.get<unknown>("/batches/physical", { params, signal });
  const result = listSchema.parse(data);
  if (result.items.some((row) => row.branch_id !== params.branch_id)) {
    throw new Error("Ответ содержит партии другой аптеки");
  }
  return result;
}
export async function setPhysicalLotBlocking(lot: PhysicalLot, blocked: boolean) {
  const { data } = await api.patch<unknown>(`/batches/physical/${lot.lot_id}/blocking`, {
    branch_id: lot.branch_id,
    is_blocked: blocked,
    expected_blocking_revision: lot.blocking_revision,
  });
  const result = blockingSchema.parse(data);
  if (
    result.lot_id !== lot.lot_id ||
    result.branch_id !== lot.branch_id ||
    result.is_blocked !== blocked
  ) {
    throw new Error("Не удалось подтвердить состояние выбранной партии");
  }
  return result;
}
export const physicalLotKeys = { all: ["inventory", "physical-lots"] as const };
export function usePhysicalLots(params: PhysicalLotFilters) {
  const { user } = useAuth();
  return useQuery({
    queryKey: [
      ...physicalLotKeys.all,
      user?.active_tenant_id,
      user?.id,
      user?.permission_scopes?.["batches.view"],
      user?.permissions,
      user?.support_access?.id,
      params,
    ],
    queryFn: ({ signal }) => listPhysicalLots(params, signal),
    enabled: Boolean(
      user?.active_tenant_id && hasPermissionForBranch(user, "batches.view", params.branch_id),
    ),
  });
}
export const physicalUnitLabels = {
  tablet: "табл.",
  capsule: "капс.",
  ampoule: "амп.",
  sachet: "пак.",
  suppository: "супп.",
} as const;
