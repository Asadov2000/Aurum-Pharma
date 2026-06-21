import { useEffect, useState } from "react";

import { Input, Label, Select, Switch } from "@/components/ui";
import { useRegistersQuery, useTenantSettingsQuery } from "@/features/foundation/queries";
import { cn } from "@/lib/utils";

import { DRAFT_TTL_MIN } from "./draftStorage";
import { ModeToggle } from "./ModeToggle";
import { SaleArea } from "./SaleArea";
import { usePosMode } from "./usePosMode";

const STORAGE_KEY = "pos:lastRegisterId";
const SOUND_KEY = "pos:beep";

export function POSPage(): JSX.Element {
  const { mode, pref, setPref } = usePosMode();
  const registers = useRegistersQuery(null, false);
  // POS draft TTL comes from tenant settings; fall back until they load (or if
  // the user can't read them).
  const settings = useTenantSettingsQuery();
  const draftTtlMin = settings.data?.draft_sale_lifetime_min ?? DRAFT_TTL_MIN;
  const [registerId, setRegisterId] = useState<string>(() => {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  });
  const [soundOn, setSoundOn] = useState<boolean>(() => {
    return window.localStorage.getItem(SOUND_KEY) === "1";
  });

  const toggleSound = (on: boolean) => {
    setSoundOn(on);
    try {
      window.localStorage.setItem(SOUND_KEY, on ? "1" : "0");
    } catch {
      // ignore
    }
  };

  // Register auto-selection:
  // - exactly one register → pick it (the cashier never chooses manually);
  // - two or more → the cashier must choose, so we only restore a still-valid
  //   last choice and otherwise leave the selector blank;
  // - zero → leave blank (the "no register" hint shows below).
  useEffect(() => {
    const list = registers.data;
    if (!list) return;
    if (list.length === 1) {
      const only = list[0];
      if (only && registerId !== only.id) setRegisterId(only.id);
    } else if (list.length >= 2 && registerId && !list.some((r) => r.id === registerId)) {
      setRegisterId("");
    }
  }, [registers.data, registerId]);

  useEffect(() => {
    if (registerId) window.localStorage.setItem(STORAGE_KEY, registerId);
  }, [registerId]);

  const registerList = registers.data;
  const onlyRegister = registerList?.length === 1 ? registerList[0] : undefined;

  return (
    <div className={cn("space-y-4", mode === "touch" ? "pos--touch" : "pos--keyboard")}>
      {/* Compact settings strip — register + POS mode + scanner sound on one
          slim row, so it doesn't eat the cashier's vertical space. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-4 py-2.5 shadow-sm">
        <h1 className="text-xl font-semibold text-foreground">Касса</h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {registerList && registerList.length === 0 ? (
            <p className="text-sm text-foreground-muted">
              Нет активных касс. Создайте кассу в разделе «Кассы».
            </p>
          ) : (
            <div className="flex items-center gap-2">
              <Label htmlFor="register" className="mb-0 text-xs text-foreground-muted">
                Касса
              </Label>
              {onlyRegister ? (
                // Single register — auto-selected, shown as a disabled field.
                <Input
                  id="register"
                  className="h-9 w-48"
                  value={onlyRegister.name}
                  readOnly
                  disabled
                />
              ) : (
                <Select
                  id="register"
                  value={registerId}
                  onChange={(e) => setRegisterId(e.target.value)}
                  className="h-9 w-48"
                >
                  <option value="">— выберите —</option>
                  {registerList?.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </Select>
              )}
            </div>
          )}
          <ModeToggle pref={pref} setPref={setPref} />
          <Switch
            label="Звук сканера"
            checked={soundOn}
            onChange={(e) => toggleSound(e.target.checked)}
          />
        </div>
      </div>

      {registerId && (
        <SaleArea
          registerId={registerId}
          mode={mode}
          soundOn={soundOn}
          draftTtlMin={draftTtlMin}
        />
      )}
    </div>
  );
}
