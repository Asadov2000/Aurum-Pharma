import { type ElectronicCheckoutResult } from "./unitElectronicCheckout";
import { PhysicalCashPanel } from "./PhysicalCashPanel";
import { PhysicalPaymentPreparationPanel } from "./PhysicalPaymentPreparationPanel";
import { type PaymentMethod } from "./types";

type Props = Parameters<typeof PhysicalCashPanel>[0] & {
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
  const {
    onElectronicCompleted,
    canResolveDecline,
    canPrepare,
    canCancel,
    paymentMethods,
    mixedPaymentEnabled,
    onPreparationBlockedChange,
    ...cash
  } = props;
  return (
    <>
      <PhysicalPaymentPreparationPanel
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
        onBlockedChange={onPreparationBlockedChange}
      />
      <PhysicalCashPanel {...cash} />
    </>
  );
}
