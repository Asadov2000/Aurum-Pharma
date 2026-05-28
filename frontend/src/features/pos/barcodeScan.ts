/**
 * Hardware barcode scanners act as keyboards: they emit the code as a burst of
 * very fast keystrokes terminated by Enter. We tell a scan apart from human
 * typing by the inter-key gap — anything slower than ~50ms resets the buffer,
 * so by the time Enter arrives only a genuine fast burst is long enough to
 * count as a scan. Pure functions here so the heuristic is unit-testable.
 */

export const SCAN_MAX_GAP_MS = 50;
export const SCAN_MIN_LENGTH = 4;

export interface ScanBuffer {
  chars: string;
  lastAt: number;
}

export function emptyScanBuffer(): ScanBuffer {
  return { chars: "", lastAt: 0 };
}

/**
 * Feed one keydown into the buffer. Returns the recognised code when Enter
 * closes a fast-enough burst of at least `minLen` chars, otherwise null.
 */
export function feedScanKey(
  buf: ScanBuffer,
  key: string,
  now: number,
  maxGap: number = SCAN_MAX_GAP_MS,
  minLen: number = SCAN_MIN_LENGTH,
): { buf: ScanBuffer; code: string | null } {
  if (key === "Enter") {
    const code = buf.chars;
    return { buf: emptyScanBuffer(), code: code.length >= minLen ? code : null };
  }
  // Only single visible characters belong to a barcode (digits/letters).
  if (key.length !== 1) {
    return { buf, code: null };
  }
  const gap = now - buf.lastAt;
  const chars = buf.chars === "" || gap <= maxGap ? buf.chars + key : key;
  return { buf: { chars, lastAt: now }, code: null };
}
