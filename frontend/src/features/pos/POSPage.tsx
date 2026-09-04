import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  Button,
  Input,
  Label,
  Modal,
  PageHeader,
  Select,
  Skeleton,
  Switch,
  TableEmpty,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { normalizePosPaymentMethods } from "@/features/foundation/paymentSettings";
import {
  useRegistersQuery,
  useTenantOperationalSettingsQuery,
} from "@/features/foundation/queries";
import { cn } from "@/lib/utils";
import { useDevicePreferences } from "@/lib/devicePreferences";

import { DRAFT_TTL_MIN } from "./draftStorage";
import { type RegisterSwitchState, SaleArea } from "./SaleArea";
import { usePosMode } from "./usePosMode";

const ModeToggle = lazy(async () => {
  const module = await import("./ModeToggle");
  return { default: module.ModeToggle };
});

export function POSPage(): JSX.Element {
  const { user } = useAuth();
  const { mode, pref, setPref } = usePosMode();
  const { preferences: devicePreferences, updatePreferences: updateDevicePreferences } =
    useDevicePreferences();
  const canOpenShift = hasPermission(user, "pos.shift_open");
  const canCloseShift = hasPermission(user, "pos.shift_close");
  const canSell = hasPermission(user, "pos.sell");
  const canManageSales = hasPermission(user, "pos.manage_sales");
  const canExportReports = hasPermission(user, "reports.export");
  const canCreateRegister = hasPermission(user, "registers.create");
  const registers = useRegistersQuery(null, false);
  // POS draft TTL comes from tenant settings; fall back until they load (or if
  // the user can't read them).
  const settings = useTenantOperationalSettingsQuery(true, true);
  const draftTtlMin = settings.data?.draft_sale_lifetime_min ?? DRAFT_TTL_MIN;
  const configuredPaymentMethods = settings.data?.pos_payment_methods;
  const paymentMethods = useMemo(
    () => (settings.data === undefined ? [] : normalizePosPaymentMethods(configuredPaymentMethods)),
    [configuredPaymentMethods, settings.data],
  );
  const mixedPaymentEnabled = settings.data?.pos_mixed_payment_enabled ?? false;
  const paymentSettingsLoading = settings.isLoading && settings.data === undefined;
  const paymentSettingsUnavailable = !settings.isLoading && settings.data === undefined;
  const [registerId, setRegisterId] = useState<string>(() => {
    return devicePreferences.lastRegisterId ?? "";
  });
  const soundOn = devicePreferences.scannerSound;
  const [registerSwitchState, setRegisterSwitchState] = useState<RegisterSwitchState>({
    blocked: false,
    hasDraft: false,
  });
  const [pendingRegisterId, setPendingRegisterId] = useState<string | null>(null);

  const updateRegisterSwitchState = useCallback((state: RegisterSwitchState) => {
    setRegisterSwitchState(state);
  }, []);

  const requestRegisterChange = (nextRegisterId: string) => {
    if (nextRegisterId === registerId || registerSwitchState.blocked) return;
    if (registerSwitchState.hasDraft) {
      setPendingRegisterId(nextRegisterId);
      return;
    }
    setRegisterId(nextRegisterId);
  };

  const confirmRegisterChange = () => {
    if (pendingRegisterId === null) return;
    setRegisterId(pendingRegisterId);
    setPendingRegisterId(null);
  };

  const toggleSound = (on: boolean) => {
    updateDevicePreferences({ scannerSound: on });
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
    if (registerId) updateDevicePreferences({ lastRegisterId: registerId });
  }, [registerId, updateDevicePreferences]);

  const registerList = registers.data;
  const onlyRegister = registerList?.length === 1 ? registerList[0] : undefined;
  const selectedRegister = registerList?.find((register) => register.id === registerId);
  const workstationControls = (
    <div className="flex w-full max-w-full flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-3">
      {registers.isLoading ? (
        <Skeleton className="h-9 w-full sm:w-52" />
      ) : registerList && registerList.length > 0 ? (
        <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto sm:flex-none">
          <Label htmlFor="register" className="mb-0 shrink-0 text-foreground-muted">
            Касса
          </Label>
          {onlyRegister ? (
            <Input
              id="register"
              className={cn(
                "min-w-0 flex-1 sm:w-44 sm:flex-none",
                mode === "touch" ? "h-12" : "h-9",
              )}
              value={onlyRegister.name}
              readOnly
              disabled
            />
          ) : (
            <Select
              id="register"
              value={registerId}
              onChange={(event) => requestRegisterChange(event.target.value)}
              disabled={registerSwitchState.blocked}
              title={
                registerSwitchState.blocked ? "Дождитесь завершения текущей операции" : undefined
              }
              className={cn(
                "min-w-0 flex-1 sm:w-44 sm:flex-none",
                mode === "touch" ? "h-12" : "h-9",
              )}
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
      <div className="w-full sm:w-auto">
        <Suspense
          fallback={
            <div
              aria-hidden="true"
              className={cn("w-full sm:w-56", mode === "touch" ? "h-12" : "h-9")}
            />
          }
        >
          <ModeToggle pref={pref} setPref={setPref} touch={mode === "touch"} />
        </Suspense>
      </div>
      <Switch
        label="Звук сканера"
        checked={soundOn}
        className={cn("w-full sm:w-auto", mode === "touch" && "min-h-12")}
        onChange={(event) => toggleSound(event.target.checked)}
      />
    </div>
  );

  return (
    <div
      className={cn(
        "space-y-4",
        registerId &&
          "xl:h-[calc(100dvh-var(--app-header-height)-2.75rem)] xl:min-h-0 xl:space-y-0",
        mode === "touch" ? "pos--touch" : "pos--keyboard",
      )}
    >
      {!registerId ? (
        <PageHeader title="Касса" compact actions={workstationControls} />
      ) : (
        <h1 className="font-display text-xl font-semibold text-foreground lg:hidden">Касса</h1>
      )}

      {registers.error && (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          <p>Не удалось загрузить рабочие кассы. Продажа пока недоступна.</p>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="mt-2"
            onClick={() => void registers.refetch()}
          >
            Повторить
          </Button>
        </div>
      )}

      {registerList && registerList.length === 0 && (
        <TableEmpty title="Нет доступной рабочей кассы">
          {canCreateRegister
            ? "Добавьте рабочую кассу в разделе «Рабочие кассы», затем вернитесь к продаже."
            : "Обратитесь к владельцу или ответственному сотруднику: он должен создать и включить рабочую кассу для этой торговой точки."}
        </TableEmpty>
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
          paymentMethods={paymentMethods}
          mixedPaymentEnabled={mixedPaymentEnabled}
          paymentSettingsLoading={paymentSettingsLoading}
          paymentSettingsUnavailable={paymentSettingsUnavailable}
          cardTerminalId={selectedRegister?.card_terminal_id}
          qrTerminalId={selectedRegister?.qr_terminal_id}
          canOpenShift={canOpenShift}
          canCloseShift={canCloseShift}
          canSell={canSell}
          canReconcileExternalPayment={canManageSales}
          canExportReports={canExportReports}
          workstationControls={workstationControls}
          onRegisterSwitchStateChange={updateRegisterSwitchState}
        />
      )}

      <Modal
        open={pendingRegisterId !== null}
        onClose={() => setPendingRegisterId(null)}
        title="Перейти на другую кассу?"
      >
        <p className="text-sm text-foreground-secondary">
          Текущий чек останется сохранённым на этой кассе. При возвращении вы сможете продолжить
          работу с ним.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setPendingRegisterId(null)}>
            Остаться
          </Button>
          <Button onClick={confirmRegisterChange}>Сохранить чек и перейти</Button>
        </div>
      </Modal>
    </div>
  );
}
