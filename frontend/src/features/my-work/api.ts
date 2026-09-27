import { api } from "@/lib/api";

import { type PersonalShiftList } from "./types";

export async function fetchPersonalShifts(
  page: number,
  signal?: AbortSignal,
): Promise<PersonalShiftList> {
  const { data } = await api.get<PersonalShiftList>("/shifts/mine", {
    params: { page, page_size: 10 },
    signal,
  });
  return data;
}
