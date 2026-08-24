import { api } from "@/lib/api";

import { type UserPreferences, type UserPreferencesUpdate } from "./types";

export async function getUserPreferences(): Promise<UserPreferences> {
  const { data } = await api.get<UserPreferences>("/auth/preferences");
  return data;
}

export async function updateUserPreferences(
  payload: UserPreferencesUpdate,
): Promise<UserPreferences> {
  const { data } = await api.patch<UserPreferences>("/auth/preferences", payload);
  return data;
}
