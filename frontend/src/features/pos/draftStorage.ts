/**
 * Per-register draft persistence for the POS register. The live draft is
 * stashed in localStorage so a reload or accidental close restores the cart;
 * a stale draft (idle past the TTL) is dropped rather than reopened silently.
 */

export const draftKey = (registerId: string): string => `pos:currentSale:${registerId}`;

// No server-side draft-lifetime setting exists yet (tenant_settings has no
// draft_sale_lifetime_min), so we enforce a sane idle TTL on the device. The
// savedAt stamp refreshes on every cart change, so this is an *idle* timeout.
export const DRAFT_TTL_MIN = 30;

export interface SavedDraft {
  saleId: string;
  nameById: Record<string, string>;
  savedAt: number;
}

export interface DraftInit {
  saleId: string | null;
  nameById: Record<string, string>;
  expired: boolean;
}

export function loadDraft(registerId: string): DraftInit {
  try {
    const raw = window.localStorage.getItem(draftKey(registerId));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<SavedDraft>;
      if (parsed && typeof parsed.saleId === "string") {
        const savedAt = typeof parsed.savedAt === "number" ? parsed.savedAt : 0;
        const ageMin = (Date.now() - savedAt) / 60_000;
        if (ageMin > DRAFT_TTL_MIN) {
          // Stale: clear it and flag the cashier instead of reopening blind.
          window.localStorage.removeItem(draftKey(registerId));
          return { saleId: null, nameById: {}, expired: true };
        }
        return { saleId: parsed.saleId, nameById: parsed.nameById ?? {}, expired: false };
      }
    }
  } catch {
    // ignore corrupt/blocked storage
  }
  return { saleId: null, nameById: {}, expired: false };
}

export function saveDraft(
  registerId: string,
  saleId: string,
  nameById: Record<string, string>,
): void {
  try {
    window.localStorage.setItem(
      draftKey(registerId),
      JSON.stringify({ saleId, nameById, savedAt: Date.now() }),
    );
  } catch {
    // ignore — just won't persist
  }
}

export function clearDraft(registerId: string): void {
  try {
    window.localStorage.removeItem(draftKey(registerId));
  } catch {
    // ignore
  }
}
