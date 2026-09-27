import { api } from "@/lib/api";

import { type PersonalProfile } from "./types";

export async function fetchPersonalProfile(signal?: AbortSignal): Promise<PersonalProfile> {
  const { data } = await api.get<PersonalProfile>("/auth/profile", { signal });
  return data;
}
