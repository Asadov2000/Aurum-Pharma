import { createCashRecovery, type CashInput, type PendingCash } from "./cashRecovery";
import {
  completePhysicalCash,
  getPhysicalCashOperation,
  savePhysicalCashIntent,
} from "./physicalCashCheckout";
import { mixedCashRecoveryKey, physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";

export { physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";
export type PendingPhysicalCash = PendingCash;
export type PhysicalCashInput = CashInput;

const recovery = createCashRecovery({
  key: physicalCashRecoveryKey,
  oppositeKey: mixedCashRecoveryKey,
  get: getPhysicalCashOperation,
  save: savePhysicalCashIntent,
  complete: completePhysicalCash,
});
export const loadPendingPhysicalCash = recovery.load;
export const runPhysicalCash = recovery.run;
export const acknowledgePhysicalCash = recovery.acknowledge;
