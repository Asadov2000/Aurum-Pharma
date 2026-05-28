import { Label, Select } from "@/components/ui";

import { type PosModePref } from "./usePosMode";

/**
 * Per-device override for the POS interaction mode. "Авто" sniffs the hardware;
 * the explicit choices force touch- or keyboard-optimised layout regardless.
 */
export function ModeToggle({
  pref,
  setPref,
}: {
  pref: PosModePref;
  setPref: (p: PosModePref) => void;
}): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="pos-mode" className="mb-0 text-xs text-foreground-muted">
        Режим POS
      </Label>
      <Select
        id="pos-mode"
        value={pref}
        onChange={(e) => setPref(e.target.value as PosModePref)}
        className="h-9 w-36"
      >
        <option value="auto">Авто</option>
        <option value="touch">Тач</option>
        <option value="keyboard">Клавиатура</option>
      </Select>
    </div>
  );
}
