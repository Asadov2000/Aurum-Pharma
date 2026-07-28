import { useEffect, useState } from "react";

import { Input, Label, PageHeader, Select, Skeleton, Switch, TableEmpty } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { useRegistersQuery, useTenantSettingsQuery } from "@/features/foundation/queries";
import { cn } from "@/lib/utils";

import { DRAFT_TTL_MIN } from "./draftStorage";
import { ModeToggle } from "./ModeToggle";
import { SaleArea } from "./SaleArea";
import { usePosMode } from "./usePosMode";

const STORAGE_KEY = "pos:lastRegisterId";
const SOUND_KEY = "pos:beep";

export function POSPage(): JSX.Element {
  const { user } = useAuth();
  const { mode, pref, setPref } = usePosMode();
  const canOpenShift = hasPermission(user, "pos.shift_open");
  const canCloseShift = hasPermission(user, "pos.shift_close");
  const canSell = hasPermission(user, "pos.sell");
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
      <PageHeader
        title="Касса"
        compact
        actions={
          <div className="flex w-full max-w-full flex-wrap items-center gap-x-4 gap-y-2 sm:justify-end">
            {registers.isLoading ? (
              <Skeleton className="h-9 w-full sm:w-52" />
            ) : registerList && registerList.length > 0 ? (
              <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
                <Label htmlFor="register" className="mb-0 shrink-0 text-foreground-muted">
                  Касса
                </Label>
                {onlyRegister ? (
                  <Input
                    id="register"
                    className="h-9 min-w-0 flex-1 sm:w-52 sm:flex-none"
                    value={onlyRegister.name}
                    readOnly
                    disabled
                  />
                ) : (
                  <Select
                    id="register"
                    value={registerId}
                    onChange={(event) => setRegisterId(event.target.value)}
                    className="h-9 min-w-0 flex-1 sm:w-52 sm:flex-none"
                  >
                    <option value="">— выберите —</option>
                    {registerList?.map((register) => (
                      <option key={register.id} value={register.id}>
                        {register.name}
                      </option>
                    ))}
                  </Select>
                )}
              </div>
            ) : null}
            <ModeToggle pref={pref} setPref={setPref} />
            <Switch
              label="Звук сканера"
              checked={soundOn}
              onChange={(event) => toggleSound(event.target.checked)}
            />
          </div>
        }
      />

      {registers.error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          Не удалось загрузить список касс. Проверьте соединение и обновите страницу.
        </p>
      )}

      {registerList && registerList.length === 0 && (
        <TableEmpty title="Нет активных касс">Создайте рабочую кассу в разделе «Кассы».</TableEmpty>
      )}

      {registerList && registerList.length > 1 && !registerId && (
        <TableEmpty title="Касса не выбрана">Выберите рабочую кассу в верхней панели.</TableEmpty>
      )}

      {registerId && (
        <SaleArea
          registerId={registerId}
          mode={mode}
          soundOn={soundOn}
          draftTtlMin={draftTtlMin}
          canOpenShift={canOpenShift}
          canCloseShift={canCloseShift}
          canSell={canSell}
        />
      )}
    </div>
  );
}
