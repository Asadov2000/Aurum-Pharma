// Mirrors backend Pydantic schemas in app/domains/onboarding/schemas.py.

export interface WizardState {
  tenant_id: string;
  current_step: number;
  steps_completed: number[];
  wizard_data: Record<string, unknown>;
  is_completed: boolean;
  started_at: string;
  completed_at: string | null;
  updated_at: string;
}

export interface Checklist {
  tenant_id: string;
  completed_tasks: string[];
  catalog_items_count: number;
  trial_eligible: boolean;
  trial_started_at: string | null;
  setup_ends_at: string;
  updated_at: string;
}

export interface StartTrialResponse {
  tenant_id: string;
  status: string;
  trial_started_at: string;
  trial_ends_at: string;
  subscription_id: string;
}
