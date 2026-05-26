import { api } from "@/lib/api";

import {
  type Notification,
  type NotificationsListParams,
  type Subscription,
} from "./types";

export async function listNotifications(
  params: NotificationsListParams,
): Promise<Notification[]> {
  const { data } = await api.get<Notification[]>("/notifications", {
    params: {
      unread_only: params.unread_only ?? false,
      severity: params.severity || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  });
  return data;
}

export async function markRead(id: string): Promise<void> {
  await api.post(`/notifications/${id}/read`);
}

export async function markAllRead(): Promise<{ marked: number }> {
  const { data } = await api.post<{ marked: number }>("/notifications/read-all");
  return data;
}

export async function listSubscriptions(): Promise<Subscription[]> {
  const { data } = await api.get<Subscription[]>("/notifications/subscriptions");
  return data;
}

export async function patchSubscriptions(items: Subscription[]): Promise<Subscription[]> {
  const { data } = await api.patch<Subscription[]>("/notifications/subscriptions", {
    items,
  });
  return data;
}
