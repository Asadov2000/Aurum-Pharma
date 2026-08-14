import { type MeResponse } from "./types";

export const PLATFORM_CAPABILITIES = {
  tenantsView: "platform.tenants.view",
  tenantsManage: "platform.tenants.manage",
  membershipsManage: "platform.memberships.manage",
  ownershipProvision: "platform.ownership.provision",
  billingManage: "platform.billing.manage",
  billingView: "platform.billing.view",
  billingPaymentReview: "platform.billing.payment.review",
  billingPaymentApprove: "platform.billing.payment.approve",
  billingAdjustmentCreate: "platform.billing.adjustment.create",
  billingAdjustmentApprove: "platform.billing.adjustment.approve",
  billingInvoiceIssue: "platform.billing.invoice.issue",
  billingPlanManage: "platform.billing.plan.manage",
  supportUse: "platform.support.use",
  syncView: "platform.sync.view",
  syncManage: "platform.sync.manage",
  auditGlobalView: "platform.audit.global.view",
  accessView: "platform.access.view",
  accessManage: "platform.access.manage",
  accountsView: "platform.accounts.view",
  accountsManage: "platform.accounts.manage",
} as const;

export type PlatformCapability = (typeof PLATFORM_CAPABILITIES)[keyof typeof PLATFORM_CAPABILITIES];

export function hasPlatformCapability(
  user: Pick<MeResponse, "platform_capabilities"> | null | undefined,
  capability: PlatformCapability,
): boolean {
  return user?.platform_capabilities.includes(capability) === true;
}
