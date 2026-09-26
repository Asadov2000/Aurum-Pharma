import { getPosCommandResult, getSale } from "./api";
import { isPendingCommand, legacyPosCommandKey, pendingPosCommandKey } from "./commandOperation";
import { draftKey, legacyDraftKey } from "./draftStorage";
import { type PosStorageScope } from "./posStorageScope";
import { type SaleDetails } from "./types";

interface RecoveryCopy {
  sourceKey: string;
  targetKey: string;
  raw: string;
}

function matchesOwner(
  sale: SaleDetails,
  saleId: string,
  registerId: string,
  scope: PosStorageScope,
) {
  return (
    sale.id === saleId &&
    sale.register_id === registerId &&
    sale.tenant_id === scope.tenantId &&
    sale.cashier_user_id === scope.userId
  );
}

/** Legacy records have no owner. Only canonical server data may assign them. */
export async function preparePosRecovery(
  registerId: string,
  scope: PosStorageScope,
  isActive: () => boolean,
): Promise<boolean> {
  try {
    if (!isActive()) return false;
    const oldDraftKey = legacyDraftKey(registerId);
    const oldCommandKey = legacyPosCommandKey(registerId);
    const draftRaw = window.localStorage.getItem(oldDraftKey);
    const commandRaw = window.localStorage.getItem(oldCommandKey);
    const copies: RecoveryCopy[] = [];

    if (draftRaw !== null) {
      const draft: unknown = JSON.parse(draftRaw);
      if (
        !draft ||
        typeof draft !== "object" ||
        !("saleId" in draft) ||
        typeof draft.saleId !== "string"
      )
        return false;
      const sale = await getSale(draft.saleId);
      if (!isActive()) return false;
      if (
        sale.id !== draft.saleId ||
        sale.register_id !== registerId ||
        sale.tenant_id !== scope.tenantId
      )
        return false;
      // A manager may receive HTTP 200 for another cashier's sale. It is not
      // their private recovery record, even with permission to manage sales.
      if (sale.cashier_user_id === scope.userId) {
        copies.push({
          sourceKey: oldDraftKey,
          targetKey: draftKey(registerId, scope),
          raw: draftRaw,
        });
      }
    }

    if (commandRaw !== null) {
      const command: unknown = JSON.parse(commandRaw);
      if (!isPendingCommand(command, registerId)) return false;
      // Never replay an unowned legacy command on 404. The request might have
      // belonged to a previous cashier; absence is not proof of ownership.
      const result = await getPosCommandResult(command.operationId);
      if (!isActive()) return false;
      if (
        result.operation_id !== command.operationId ||
        typeof result.sale_id !== "string" ||
        result.result.command_type !== command.commandType ||
        (command.commandType !== "sale.create" && result.sale_id !== command.saleId)
      )
        return false;
      const sale = await getSale(result.sale_id);
      if (!isActive() || !matchesOwner(sale, result.sale_id, registerId, scope)) return false;
      copies.push({
        sourceKey: oldCommandKey,
        targetKey: pendingPosCommandKey(registerId, scope),
        raw: commandRaw,
      });
    }

    if (!isActive()) return false;
    // Check every original before writing anything, so a concurrent legacy
    // tab or an existing private draft cannot be silently overwritten.
    if (
      window.localStorage.getItem(oldDraftKey) !== draftRaw ||
      window.localStorage.getItem(oldCommandKey) !== commandRaw
    )
      return false;
    for (const copy of copies) {
      const target = window.localStorage.getItem(copy.targetKey);
      if (target !== null && target !== copy.raw) return false;
    }
    for (const copy of copies) {
      window.localStorage.setItem(copy.targetKey, copy.raw);
      if (window.localStorage.getItem(copy.targetKey) !== copy.raw) return false;
    }
    // Keep the original savedAt and sale-keyed idempotency/payment markers.
    // Delete only after the complete recovery state has been copied durably.
    for (const copy of copies) {
      if (!isActive() || window.localStorage.getItem(copy.sourceKey) !== copy.raw) return false;
      window.localStorage.removeItem(copy.sourceKey);
      if (window.localStorage.getItem(copy.sourceKey) !== null) return false;
    }
    return true;
  } catch {
    // Network, authorization and storage failures preserve all unresolved data.
    return false;
  }
}
