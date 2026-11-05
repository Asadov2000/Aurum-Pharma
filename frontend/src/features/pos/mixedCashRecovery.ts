import { createCashRecovery, type CashInput, type PendingCash } from "./cashRecovery";
import { completeMixedCash, getMixedCashOperation, saveMixedCashIntent } from "./mixedCashCheckout";
import { mixedCashRecoveryKey, physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";

export { mixedCashRecoveryKey } from "./physicalCashRecoveryPresence";
export type PendingMixedCash = PendingCash;
export type MixedCashInput = CashInput;

const recovery = createCashRecovery({
  key: mixedCashRecoveryKey,
  oppositeKey: physicalCashRecoveryKey,
  get: getMixedCashOperation,
  save: saveMixedCashIntent,
  complete: completeMixedCash,
});
export const loadPendingMixedCash = recovery.load;
export const runMixedCash = recovery.run;
export const acknowledgeMixedCash = recovery.acknowledge;
