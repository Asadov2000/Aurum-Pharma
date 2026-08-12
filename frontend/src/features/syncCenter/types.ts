export type SyncMonitoringHealth = "healthy" | "delayed" | "offline" | "critical" | "revoked";
export type SyncMonitoringMode = "shadow_readonly" | "edge_writer";
export type SyncNodeStatus = "active" | "revoked";
export type SyncContactState = "recent" | "stale" | "offline" | "never_seen";
export type SyncIntegrityState = "verified" | "stale_report" | "unverified" | "mismatch";
export type SyncReportStatus = "matched" | "mismatch";

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
