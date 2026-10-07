import { create } from "zustand";

import { type SupportAccessSession } from "@/features/supportAccess/types";

interface SupportAccessState {
  active: SupportAccessSession | null;
  revision: number;
  setActive: (session: SupportAccessSession) => void;
  clear: () => void;
}

export const useSupportAccessStore = create<SupportAccessState>((set) => ({
  active: null,
  revision: 0,
  setActive: (active) => set((state) => ({ active, revision: state.revision + 1 })),
  clear: () => set((state) => ({ active: null, revision: state.revision + 1 })),
}));

export function getSupportAccessSessionId(): string | null {
  return useSupportAccessStore.getState().active?.id ?? null;
}

export function getSupportAccessRevision(): number {
  return useSupportAccessStore.getState().revision;
}
