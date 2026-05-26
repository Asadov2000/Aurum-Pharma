// Mirrors backend Pydantic schemas in app/domains/notifications/schemas.py.

export type Severity = "info" | "warning" | "error" | "critical";
export type Channel = "in_app" | "email" | "telegram" | "sms";

export interface Notification {
  id: string;
  tenant_id: string;
  user_id: string;
  event_type: string;
  title: string;
  body: string | null;
  data: Record<string, unknown> | null;
  severity: Severity;
  read_at: string | null;
  created_at: string;
}

export interface Subscription {
  event_type: string;
  channels: Channel[];
  is_enabled: boolean;
}

export interface NotificationsListParams {
  unread_only?: boolean;
  severity?: Severity;
  page?: number;
  page_size?: number;
}
