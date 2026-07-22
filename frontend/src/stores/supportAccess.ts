import { create } from "zustand";

import { type SupportAccessSession } from "@/features/supportAccess/types";

interface SupportAccessState {
  active: SupportAccessSession | null;
  setActive: (session: SupportAccessSession) => void;
  clear: () => void;
}

export const useSupportAccessStore = create<SupportAccessState>((set) => ({
  active: null,
  setActive: (active) => set({ active }),
  clear: () => set({ active: null }),
}));

export function getSupportAccessSessionId(): string | null {
  return useSupportAccessStore.getState().active?.id ?? null;
}
