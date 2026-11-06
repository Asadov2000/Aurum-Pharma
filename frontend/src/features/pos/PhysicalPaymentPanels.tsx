import { useCallback, useRef, useState } from "react";

import { type ElectronicCheckoutResult } from "./unitElectronicCheckout";
import { PhysicalCashPanel } from "./PhysicalCashPanel";
import { PhysicalPaymentPreparationPanel } from "./PhysicalPaymentPreparationPanel";
import { PosPaymentMethodButtons } from "./PosPaymentMethodButtons";
import { type PaymentMethod } from "./types";

type Method = PaymentMethod | "mixed";
const methodLabels: Record<Method, string> = {
  cash: "Наличные",
  card: "Карта",
  qr: "QR",
  mixed: "Смешанная",
};

type Props = Parameters<typeof PhysicalCashPanel>[0] & {
  displayTotal?: string | null;
  onElectronicCompleted?: (result: ElectronicCheckoutResult) => Promise<boolean>;
  canResolveDecline?: boolean;
  canPrepare: boolean;
  canCancel: boolean;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  onPreparationBlockedChange: (blocked: boolean) => void;
};

/** Load physical payment controls together, outside the initial POS route. */
export function PhysicalPaymentPanels(props: Props): JSX.Element {
  const latest = useRef(props);
  latest.current = props;
  const [selected, setSelected] = useState<Method>(
    props.paymentMethods.includes("cash") ? "cash" : (props.paymentMethods[0] ?? "cash"),
  );
  const [cashBlocked, setCashBlocked] = useState(false);
  const [preparationBlocked, setPreparationBlocked] = useState(true);
  const [cashTotal, setCashTotal] = useState<string | null>(null);
  const [electronicTotal, setElectronicTotal] = useState<string | null>(null);
  const onCashBlocked = useCallback((blocked: boolean) => {
    setCashBlocked(blocked);
    if (blocked) setSelected("cash");
    latest.current.onBlockedChange(blocked);
  }, []);
  const onPreparationBlocked = useCallback((blocked: boolean) => {
    setPreparationBlocked(blocked);
    latest.current.onPreparationBlockedChange(blocked);
  }, []);
  const onRecoveryMethod = useCallback((method: Exclude<Method, "cash"> | null) => {
    if (method) setSelected(method);
  }, []);
  const {
    onElectronicCompleted,
    canResolveDecline,
    canPrepare,
    canCancel,
    paymentMethods,
    mixedPaymentEnabled,
    ...cash
  } = props;
  const options = (["cash", "card", "qr", "mixed"] as const)
    .filter((method) =>
      method === "mixed"
        ? mixedPaymentEnabled && paymentMethods.length > 1
        : paymentMethods.includes(method),
    )
    .map((value) => ({ value, label: methodLabels[value] }));
  const blocked = cashBlocked || preparationBlocked;
  const method =
    blocked || options.some((option) => option.value === selected)
      ? selected
      : (options[0]?.value ?? "cash");
  const showCash = cashBlocked || (method === "cash" && !preparationBlocked);
  const showElectronic = preparationBlocked || (method !== "cash" && !cashBlocked);
  const total =
    preparationBlocked && electronicTotal
      ? electronicTotal
      : cashBlocked && cashTotal
        ? cashTotal
        : props.displayTotal !== undefined
          ? props.displayTotal
          : (cashTotal ?? electronicTotal);
  return (
    <section
      aria-label="Оплата"
      className="space-y-3 rounded-[10px] border border-border bg-surface p-4"
    >
      <h2 className="text-base font-semibold">Оплата</h2>
      <div>
        <p className="text-[32px] font-semibold leading-tight tracking-tight tabular-nums">
          {total?.replace(".", ",") ?? "—"}
        </p>
        <p className="mt-1 text-xs text-foreground-muted">сомони к оплате</p>
      </div>
      <PosPaymentMethodButtons
        options={options}
        value={method}
        onChange={setSelected}
        disabled={blocked || !props.online || (!canPrepare && !cash.canPay)}
      />
      {/* Payment owners stay mounted: hiding a method must never discard recovery. */}
      <div hidden={!showElectronic}>
        <PhysicalPaymentPreparationPanel
          embedded
          selectedMethod={method === "cash" ? undefined : method}
          onRecoveryMethodChange={onRecoveryMethod}
          onTotalChange={setElectronicTotal}
          saleId={cash.saleId}
          branchId={cash.branchId}
          registerId={cash.registerId}
          physicalOnly={cash.kind !== "mixed"}
          onElectronicCompleted={onElectronicCompleted}
          scope={cash.scope}
          online={cash.online}
          canPrepare={canPrepare}
          canCancel={canCancel}
          canResolveDecline={canResolveDecline}
          paymentMethods={paymentMethods}
          mixedPaymentEnabled={mixedPaymentEnabled}
          sessionIsActive={cash.sessionIsActive}
          beforeStart={cash.beforeStart}
          onBlockedChange={onPreparationBlocked}
        />
      </div>
      <div hidden={!showCash}>
        <PhysicalCashPanel
          {...cash}
          embedded
          onTotalChange={setCashTotal}
          onBlockedChange={onCashBlocked}
        />
      </div>
    </section>
  );
}
