import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listNotifications,
  listSubscriptions,
  markAllRead,
  markRead,
  patchSubscriptions,
} from "./api";
import { type NotificationsListParams, type Subscription } from "./types";

export const notificationsKeys = {
  list: (params: NotificationsListParams) => ["notifications", "list", params] as const,
  subscriptions: ["notifications", "subscriptions"] as const,
};

export function useNotificationsQuery(params: NotificationsListParams, enabled = true) {
  return useQuery({
    queryKey: notificationsKeys.list(params),
    queryFn: () => listNotifications(params),
    enabled,
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => markRead(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["notifications", "list"] });
    },
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: markAllRead,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["notifications", "list"] });
    },
  });
}

export function useSubscriptionsQuery(enabled = true) {
  return useQuery({
    queryKey: notificationsKeys.subscriptions,
    queryFn: listSubscriptions,
    enabled,
  });
}

export function usePatchSubscriptions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: Subscription[]) => patchSubscriptions(items),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: notificationsKeys.subscriptions });
    },
  });
}
