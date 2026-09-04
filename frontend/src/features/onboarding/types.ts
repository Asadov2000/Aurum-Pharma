export type ReadinessStepCode =
  | "pharmacy_profile"
  | "licensed_branch"
  | "receipt_details"
  | "tenant_owner"
  | "catalog"
  | "pos_settings"
  | "regulatory"
  | "ready";

export type ReadinessTaskCode =
  | "first_incoming"
  | "first_sale"
  | "second_user"
  | "shift_opened"
  | "test_receipt_printed";

export type TenantLaunchStatus =
  | "setup"
  | "trial"
  | "active"
  | "grace_period"
  | "readonly"
  | "archived";

export interface ReadinessStep {
  code: ReadinessStepCode;
  is_complete: boolean;
  required: boolean;
  current: number | null;
  target: number | null;
  action_hint?:
    | "register_missing"
    | "payment_methods_missing"
    | "operational_branch_missing"
    | null;
}

export interface ReadinessTask {
  code: ReadinessTaskCode;
  is_complete: boolean;
}

export interface OnboardingOverview {
  tenant_id: string;
  tenant_name: string;
  tenant_status: TenantLaunchStatus;
  setup_ends_at: string;
  trial_started_at: string | null;
  trial_ends_at: string | null;
  subscription_id: string | null;
  steps: ReadinessStep[];
  recommended_tasks: ReadinessTask[];
  required_completed: number;
  required_total: number;
  recommended_completed: number;
  recommended_total: number;
  is_ready: boolean;
  can_start_trial: boolean;
  blocker_codes: ReadinessStepCode[];
}

export interface StartTrialResponse {
  tenant_id: string;
  status: TenantLaunchStatus;
  trial_started_at: string;
  trial_ends_at: string;
  subscription_id: string;
}
