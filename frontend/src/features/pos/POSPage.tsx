import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ContextHelpPortal } from "@/components/layout/ContextHelpPortal";

import {
  Button,
  Input,
  Label,
  PageHeader,
  Select,
  Skeleton,
  Switch,
  TableEmpty,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission, hasPermissionForBranch } from "@/features/auth/permissions";
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

const RegisterSwitchNotice = lazy(async () => {
  const module = await import("./RegisterSwitchNotice");
  return { default: module.RegisterSwitchNotice };
});

export function POSPage(): JSX.Element {
  const { user } = useAuth();
  const { mode, pref, setPref } = usePosMode();
  const { preferences: devicePreferences, updatePreferences: updateDevicePreferences } =
    useDevicePreferences();
  const canCreateRegister = hasPermission(user, "registers.create");
  const registers = useRegistersQuery(null, false);
  const registerList = useMemo(
    () => registers.data?.filter((register) => register.is_active),
    [registers.data],
  );
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
    blocked: Boolean(registerId),
    hasDraft: false,
  });
  const switchStateRef = useRef(registerSwitchState);
  const activeRegisterRef = useRef(registerId);
  const [pendingRegisterId, setPendingRegisterId] = useState<string | null>(null);

  const selectRegister = useCallback((nextRegisterId: string) => {
    activeRegisterRef.current = nextRegisterId;
    const state = { blocked: Boolean(nextRegisterId), hasDraft: false };
    switchStateRef.current = state;
    setRegisterSwitchState(state);
    setRegisterId(nextRegisterId);
  }, []);
  const updateRegisterSwitchState = useCallback(
    (state: RegisterSwitchState) => {
      if (activeRegisterRef.current !== registerId) return;
      switchStateRef.current = state;
      setRegisterSwitchState(state);
    },
    [registerId],
  );
  const canSelectRegister = (id: string) =>
    !registers.isError && (id === "" || registerList?.some((register) => register.id === id));

  const requestRegisterChange = (nextRegisterId: string) => {
    if (
      nextRegisterId === registerId ||
      switchStateRef.current.blocked ||
      !canSelectRegister(nextRegisterId)
    )
      return;
    if (switchStateRef.current.hasDraft) {
      setPendingRegisterId(nextRegisterId);
      return;
    }
    selectRegister(nextRegisterId);
  };

  const confirmRegisterChange = () => {
    if (
      pendingRegisterId === null ||
      switchStateRef.current.blocked ||
      !canSelectRegister(pendingRegisterId)
    )
      return;
    selectRegister(pendingRegisterId);
    setPendingRegisterId(null);
  };

  const toggleSound = (on: boolean) => {
    updateDevicePreferences({ scannerSound: on });
  };

  // Never replace a workspace until its recovery/draft state is known and clear.
  useEffect(() => {
    if (!registerList || registers.isError) return;
    if (registerList.some((register) => register.id === registerId)) return;
    if (registerId && (switchStateRef.current.blocked || switchStateRef.current.hasDraft)) return;
    const nextRegisterId = registerList.length === 1 ? (registerList[0]?.id ?? "") : "";
    if (nextRegisterId !== registerId) selectRegister(nextRegisterId);
  }, [registerList, registerId, registerSwitchState, registers.isError, selectRegister]);

  useEffect(() => {
    if (
      pendingRegisterId !== null &&
      (registerSwitchState.blocked ||
        registers.isError ||
        (pendingRegisterId !== "" &&
          !registerList?.some((register) => register.id === pendingRegisterId)))
    )
      setPendingRegisterId(null);
  }, [pendingRegisterId, registerList, registerSwitchState.blocked, registers.isError]);

  useEffect(() => {
    if (registerId) updateDevicePreferences({ lastRegisterId: registerId });
  }, [registerId, updateDevicePreferences]);

  const onlyRegister = registerList?.length === 1 ? registerList[0] : undefined;
  const selectedRegister = registerList?.find((register) => register.id === registerId);
  const registerAvailable = Boolean(selectedRegister) && !registers.isError;
  const registerUnavailable = Boolean(registerId && registerList && !selectedRegister);
  const canOpenShift = hasPermissionForBranch(user, "pos.shift_open", selectedRegister?.branch_id);
  const canCloseShift = hasPermissionForBranch(
    user,
    "pos.shift_close",
    selectedRegister?.branch_id,
  );
  const canSell = hasPermissionForBranch(user, "pos.sell", selectedRegister?.branch_id);
  const canManageSales = hasPermissionForBranch(
    user,
    "pos.manage_sales",
    selectedRegister?.branch_id,
  );
  const canExportReports = hasPermissionForBranch(
    user,
    "reports.export",
    selectedRegister?.branch_id,
  );
  const workstationControls = (
    <div className="flex w-full max-w-full flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-3">
      {registers.isLoading ? (
        <Skeleton className="h-9 w-full sm:w-52" />
      ) : registerList && (registerList.length > 0 || registerId) ? (
        <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto sm:flex-none">
          <Label htmlFor="register" className="mb-0 shrink-0 text-foreground-muted">
            Касса
          </Label>
          {onlyRegister?.id === registerId ? (
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
              {registerId && !selectedRegister ? (
                <option value={registerId}>Текущая касса недоступна</option>
              ) : null}
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
      <ContextHelpPortal pathname="/pos" />
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

      {registerUnavailable || pendingRegisterId !== null ? (
        <Suspense
          fallback={
            registerUnavailable ? (
              <p role="status">Касса недоступна. Новые операции запрещены.</p>
            ) : null
          }
        >
          <RegisterSwitchNotice
            unavailable={registerUnavailable}
            confirmationOpen={pendingRegisterId !== null}
            confirmBlocked={
              registerSwitchState.blocked || !canSelectRegister(pendingRegisterId ?? "")
            }
            onCancel={() => setPendingRegisterId(null)}
            onConfirm={confirmRegisterChange}
          />
        </Suspense>
      ) : null}

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
          registerAvailable={registerAvailable}
          mode={mode}
          soundOn={soundOn}
          draftTtlMin={draftTtlMin}
          paymentMethods={paymentMethods}
          mixedPaymentEnabled={mixedPaymentEnabled}
          paymentSettingsLoading={paymentSettingsLoading}
          paymentSettingsUnavailable={paymentSettingsUnavailable}
          cardTerminalId={selectedRegister?.card_terminal_id}
          qrTerminalId={selectedRegister?.qr_terminal_id}
          canOpenShift={canOpenShift && registerAvailable}
          canCloseShift={canCloseShift && registerAvailable}
          canSell={canSell}
          canReconcileExternalPayment={canManageSales && registerAvailable}
          canExportReports={canExportReports}
          workstationControls={workstationControls}
          onRegisterSwitchStateChange={updateRegisterSwitchState}
        />
      )}
    </div>
  );
}
