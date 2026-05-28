import { useEffect, useState } from "react";

import { Card, CardContent, Label, Select, Switch } from "@/components/ui";
import { useRegistersQuery } from "@/features/foundation/queries";
import { cn } from "@/lib/utils";

import { ModeToggle } from "./ModeToggle";
import { SaleArea } from "./SaleArea";
import { usePosMode } from "./usePosMode";

const STORAGE_KEY = "pos:lastRegisterId";
const SOUND_KEY = "pos:beep";

export function POSPage(): JSX.Element {
  const { mode, pref, setPref } = usePosMode();
  const registers = useRegistersQuery(null, false);
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

  // Auto-select the first active register the first time the user lands.
  useEffect(() => {
    if (!registerId && registers.data && registers.data.length > 0) {
      const first = registers.data[0];
      if (first) setRegisterId(first.id);
    }
  }, [registers.data, registerId]);

  useEffect(() => {
    if (registerId) window.localStorage.setItem(STORAGE_KEY, registerId);
  }, [registerId]);

  return (
    <div className={cn("space-y-4", mode === "touch" ? "pos--touch" : "pos--keyboard")}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold text-foreground">Касса</h1>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <ModeToggle pref={pref} setPref={setPref} />
            <Switch
              label="Звук сканера"
              checked={soundOn}
              onChange={(e) => toggleSound(e.target.checked)}
            />
          </div>
          <Card className="shrink-0">
            <CardContent className="flex items-end gap-3 py-3">
              <div>
                <Label htmlFor="register">Касса</Label>
                <Select
                  id="register"
                  value={registerId}
                  onChange={(e) => setRegisterId(e.target.value)}
                  className="w-56"
                >
                  <option value="">— выберите —</option>
                  {registers.data?.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </Select>
              </div>
              {registers.data?.length === 0 && (
                <p className="text-sm text-foreground-muted">
                  Нет активных касс. Создайте кассу в разделе «Кассы».
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {registerId && <SaleArea registerId={registerId} mode={mode} soundOn={soundOn} />}
    </div>
  );
}
