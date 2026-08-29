export type SyncMonitoringHealth = "healthy" | "delayed" | "offline" | "critical" | "revoked";
export type SyncMonitoringMode = "shadow_readonly" | "edge_writer";
export type SyncNodeStatus = "active" | "revoked";
export type SyncContactState = "recent" | "stale" | "offline" | "never_seen";
export type SyncIntegrityState = "verified" | "stale_report" | "unverified" | "mismatch";
export type SyncReportStatus = "matched" | "mismatch";
export type SyncQuarantineStatus = "gap" | "quarantined" | "mismatch";

export interface SyncMonitoringSummary {
  total_nodes: number;
  healthy_nodes: number;
  delayed_nodes: number;
  offline_nodes: number;
  critical_nodes: number;
  revoked_nodes: number;
  never_connected_nodes: number;
  expiring_credentials: number;
  pending_handovers: number;
  pending_credential_rotations: number;
  quarantined_nodes: number;
}

export interface SyncMonitoringTenant {
  tenant_id: string;
  tenant_name: string;
  node_count: number;
}

export interface SyncMonitoringNode {
  node_id: string;
  tenant_id: string;
  tenant_name: string;
  branch_id: string;
  branch_name: string;
  register_id: string | null;
  register_name: string | null;
  display_name: string;
  mode: SyncMonitoringMode;
  node_status: SyncNodeStatus;
  health: SyncMonitoringHealth;
  contact_state: SyncContactState;
  integrity_state: SyncIntegrityState;
  credential_expires_at: string;
  last_seen_at: string | null;
  latest_report_at: string | null;
  latest_report_status: SyncReportStatus | null;
  source_verified: boolean | null;
  writer_epoch: number;
  current_sequence: number;
  reported_sequence: number | null;
  lag_events: number;
  lifecycle_version: number;
  credential_rotation_id: string | null;
  credential_rotation_status: "pending" | "verified" | "expired" | null;
  credential_rotation_activate_before: string | null;
  credential_rotation_verified_at: string | null;
  quarantine_incident_count: number;
  latest_quarantine_reason: string | null;
  latest_quarantine_status: SyncQuarantineStatus | null;
  latest_quarantine_sequence: number | null;
  latest_quarantine_at: string | null;
}

export interface SyncMonitoringOverview {
  generated_at: string;
  summary: SyncMonitoringSummary;
  tenants: SyncMonitoringTenant[];
  items: SyncMonitoringNode[];
  total: number;
  limit: number;
  offset: number;
}

export interface SyncMonitoringFilters {
  tenant_id?: string;
  health?: SyncMonitoringHealth;
  mode?: SyncMonitoringMode;
  q?: string;
  limit: number;
  offset: number;
}

export type SyncNodeActionReasonCode =
  | "routine_maintenance"
  | "credential_expiry"
  | "security_incident"
  | "device_replacement"
  | "device_retired"
  | "other";

export interface SyncNodeActionPayload {
  expected_version: number;
  operation_id: string;
  confirmation_name: string;
  reason_code: SyncNodeActionReasonCode;
  reason: string;
}

export interface SyncCredentialRotationStartPayload extends SyncNodeActionPayload {
  credential_valid_days: number;
}

export interface SyncCredentialRotationSecret {
  rotation_id: string;
  node_id: string;
  status: "pending" | "verified" | "completed" | "cancelled";
  node_version: number;
  credential_issued_at: string;
  credential_expires_at: string;
  activate_before: string;
  verified_at: string | null;
  credential: string | null;
  replayed: boolean;
}

export interface SyncCredentialRotationTransition {
  rotation_id: string;
  node_id: string;
  rotation_status: "pending" | "verified" | "completed" | "cancelled";
  node_status: SyncNodeStatus;
  node_version: number;
  replayed: boolean;
}

export interface SyncNodeLifecycleResult {
  node_id: string;
  node_status: SyncNodeStatus;
  node_version: number;
  replayed: boolean;
}

export type SyncNodeAction = "rotate" | "complete" | "cancel" | "revoke";
