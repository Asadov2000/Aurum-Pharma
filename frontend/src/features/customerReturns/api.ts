import { api } from "@/lib/api";
import { z } from "zod";

import {
  type CustomerReturnItem,
  type CustomerReturnList,
  type CustomerReturnSearchParams,
  type ResolveCustomerReturnPayload,
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
