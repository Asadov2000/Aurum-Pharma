import { Label, SegmentedControl, type SegmentOption } from "@/components/ui";

import { type PosModePref } from "./usePosMode";

const options: readonly SegmentOption<PosModePref>[] = [
  { value: "auto", label: "Авто", title: "Выбрать режим по устройству" },
  { value: "touch", label: "Сенсор", title: "Крупные элементы для сенсорного экрана" },
  { value: "keyboard", label: "Клавиши", title: "Компактный режим с горячими клавишами" },
];

/**
 * Per-device override for the POS interaction mode. "Авто" sniffs the hardware;
 * the explicit choices force touch- or keyboard-optimised layout regardless.
 */
export function ModeToggle({
  pref,
  setPref,
  touch = false,
}: {
  pref: PosModePref;
  setPref: (p: PosModePref) => void;
  touch?: boolean;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label className="mb-0 text-foreground-muted">Режим</Label>
      <SegmentedControl
        value={pref}
        options={options}
        onChange={setPref}
        label="Режим кассы"
        size={touch ? "lg" : "sm"}
      />
    </div>
  );
}
