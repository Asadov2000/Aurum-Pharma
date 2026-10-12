import { api } from "@/lib/api";
import { z } from "zod";

import {
  type CustomerReturnItem,
  type CustomerReturnList,
  type CustomerReturnSearchParams,
  type ResolveCustomerReturnPayload,
  type MixedCustomerReturnList,
  type MixedCustomerReturnSearchParams,
  type PhysicalCustomerReturnList,
  type PhysicalCustomerReturnSearchParams,
} from "./types";

export async function listCustomerReturns(
  params: CustomerReturnSearchParams,
): Promise<CustomerReturnList> {
  const { data } = await api.get<CustomerReturnList>("/customer-returns", {
    params: {
      status: params.status,
      branch_id: params.branch_id || undefined,
      search: params.search || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
  });
  return data;
}

export async function resolveCustomerReturn(
  id: string,
  payload: ResolveCustomerReturnPayload,
): Promise<CustomerReturnItem> {
  const { data } = await api.post<CustomerReturnItem>(`/customer-returns/${id}/resolve`, payload);
  return data;
}

const physicalReturnItemSchema = z
  .object({
    operation_id: z.string().uuid(),
    line_id: z.string().uuid(),
    lot_id: z.string().uuid(),
    branch_id: z.string().uuid(),
    branch_name: z.string().min(1),
    report_timezone: z
      .string()
      .min(1)
      .refine((value) => {
        try {
          new Intl.DateTimeFormat("ru-RU", { timeZone: value });
          return true;
        } catch {
          return false;
        }
      }),
    parent_sale_id: z.string().uuid(),
    parent_receipt_number: z.string(),
    return_sale_id: z.string().uuid().nullable(),
    return_receipt_number: z.string().nullable(),
    catalog_id: z.string().uuid(),
    catalog_name: z.string().min(1),
    base_units: z.number().int().positive().safe(),
    unit_label: z.string().min(1),
    batch_number: z.string().nullable(),
    expires_at: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    received_at: z.string().datetime({ offset: true }),
    refund_reason: z.string().nullable(),
    refund_comment: z.string().nullable(),
    intake_kind: z.enum(["cash_refund", "zero_return"]),
    status: z.literal("pending"),
  })
  .strict()
  .refine((item) =>
    item.intake_kind === "zero_return"
      ? item.return_sale_id === null && item.return_receipt_number === null
      : true,
  );
const physicalReturnListSchema = z
  .object({
    items: z.array(physicalReturnItemSchema),
    total: z.number().int().nonnegative().safe(),
    page: z.number().int().positive().safe(),
    page_size: z.number().int().positive().max(200),
  })
  .strict();

export async function listPhysicalCustomerReturns(
  params: PhysicalCustomerReturnSearchParams,
): Promise<PhysicalCustomerReturnList> {
  const { data } = await api.get<unknown>("/customer-returns/physical", {
    params: {
      branch_id: params.branch_id || undefined,
      search: params.search || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
  });
  return physicalReturnListSchema.parse(data);
}

const mixedReturnBaseSchema = physicalReturnItemSchema
  .innerType()
  .omit({ lot_id: true, base_units: true, unit_label: true })
  .extend({
    catalog_name: z.string().min(1).max(500),
    refund_reason: z
      .enum([
        "dispensing_error",
        "duplicate_sale",
        "pricing_error",
        "quality_issue",
        "damaged_package",
        "customer_cancelled",
        "other",
      ])
      .nullable(),
  });
const mixedLegacyReturnItemSchema = mixedReturnBaseSchema
  .extend({
    inventory_kind: z.literal("legacy_pack"),
    batch_id: z.string().uuid(),
    lot_id: z.null(),
    legacy_quantity: z
      .string()
      .regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/)
      .refine((value) => value !== "0.000"),
    base_units: z.null(),
    unit_label: z.literal("уп."),
  })
  .strict();
const mixedPhysicalReturnItemSchema = mixedReturnBaseSchema
  .extend({
    inventory_kind: z.literal("physical_unit"),
    batch_id: z.null(),
    lot_id: z.string().uuid(),
    legacy_quantity: z.null(),
    base_units: z.number().int().positive().safe().max(99_999_999_999),
    unit_label: z.enum(["уп.", "табл.", "капс.", "амп.", "саше", "супп."]),
  })
  .strict();
const mixedReturnItemSchema = z
  .union([mixedLegacyReturnItemSchema, mixedPhysicalReturnItemSchema])
  .refine((item) =>
    item.intake_kind === "zero_return"
      ? item.return_sale_id === null && item.return_receipt_number === null
      : item.return_sale_id !== null && item.return_receipt_number !== null,
  );
const mixedReturnListSchema = z
  .object({
    items: z.array(mixedReturnItemSchema),
    total: z.number().int().nonnegative().safe(),
    page: z.number().int().positive().max(2_147_483_647),
    page_size: z.number().int().positive().max(200),
  })
  .strict();
export async function listMixedCustomerReturns(
  params: MixedCustomerReturnSearchParams,
): Promise<MixedCustomerReturnList> {
  const { data } = await api.get<unknown>("/customer-returns/mixed", {
    params: {
      branch_id: params.branch_id || undefined,
      search: params.search || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
  });
  return mixedReturnListSchema.parse(data);
}
