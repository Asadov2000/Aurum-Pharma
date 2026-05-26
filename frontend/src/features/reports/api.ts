// Reports today are limited to the POS Z-report. Backend has no
// dedicated /reports namespace yet — this file is the seam where
// future report endpoints will plug in.

import { api } from "@/lib/api";
import { type ZReport } from "@/features/pos/types";

export async function getZReport(shiftId: string): Promise<ZReport> {
  const { data } = await api.get<ZReport>(`/shifts/${shiftId}/z-report`);
  return data;
}
