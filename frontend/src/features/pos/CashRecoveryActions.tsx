import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";

import { type CashCheckoutResult } from "./PhysicalCashPanel";
import { getMixedCashOperation } from "./mixedCashCheckout";
import { getPhysicalCashOperation } from "./physicalCashCheckout";
import {
  acknowledgeCancelledPhysicalCash,
  cancelPendingPhysicalCash,
} from "./physicalCashCancellation";
import { type PendingPhysicalCash } from "./physicalCashRecovery";

interface Props {
  pending: PendingPhysicalCash;
  context: string;
  kind: "physical" | "mixed";
  initial: boolean;
  online: boolean;
  busy: boolean;
  active: () => boolean;
  onContinue: () => Promise<void>;
  onCompleted: (result: CashCheckoutResult) => Promise<void>;
  onCancelled: () => void;
  onError: (error: unknown) => void;
}

export default function CashRecoveryActions(props: Props): JSX.Element {
  const latest = useRef(props);
  latest.current = props;
  const [identity] = useState(() => JSON.stringify([props.pending, props.context, props.kind]));
  const mounted = useRef(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const [busy, setBusy] = useState(false);
  const active = useCallback((): boolean => {
    const owner = latest.current;
    return (
      mounted.current &&
      owner.active() &&
      JSON.stringify([owner.pending, owner.context, owner.kind]) === identity
    );
  }, [identity]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!props.initial || !props.online || busyRef.current || latest.current.busy) return;
    const controller = new AbortController();
    const attempt = ++generation.current;
    busyRef.current = true;
    setBusy(true);
    let cancelled = false;
    let applying = false;
    const ownsBusy = () => generation.current === attempt;
    // The parent establishes its mounted fence in its passive effect first.
    void Promise.resolve()
      .then(async () => {
        if (cancelled || !active()) return;
        const owner = latest.current;
        const scope = { tenantId: owner.pending.tenantId, userId: owner.pending.userId };
        const get = owner.kind === "physical" ? getPhysicalCashOperation : getMixedCashOperation;
        const status = await get(owner.pending.request, scope, controller.signal);
        if (cancelled || !active()) return;
        if (status.state === "completed") {
          applying = true;
          await owner.onCompleted(status.result);
        } else if (status.state === "cancelled" && owner.kind === "physical") {
          applying = true;
          const result = await acknowledgeCancelledPhysicalCash(
            owner.pending,
            active,
            controller.signal,
          );
          if (cancelled || !active()) return;
          if (result?.state === "completed") await owner.onCompleted(result.result);
          else if (result?.state === "cancelled") owner.onCancelled();
          else throw new Error("Отмена оплаты ещё не подтверждена.");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && active()) latest.current.onError(error);
      })
      .finally(() => {
        if (ownsBusy()) {
          busyRef.current = false;
          if (mounted.current) setBusy(false);
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
      // An aborted read may release only its own busy slot. Never release an
      // in-flight receipt acknowledgement or a newer explicit operator action.
      if (!applying && ownsBusy()) busyRef.current = false;
    };
  }, [props.initial, props.online, props.kind, active]);

  async function act(cancel: boolean): Promise<void> {
    if (busyRef.current || latest.current.busy || !latest.current.online || !active()) return;
    const owner = latest.current;
    const attempt = ++generation.current;
    busyRef.current = true;
    setBusy(true);
    try {
      if (!cancel) await owner.onContinue();
      else {
        const result = await cancelPendingPhysicalCash(owner.pending, active);
        if (!active()) return;
        if (result?.state === "completed") await owner.onCompleted(result.result);
        else if (result?.state === "cancelled") owner.onCancelled();
        else throw new Error("Отмена оплаты ещё не подтверждена.");
      }
    } catch (error: unknown) {
      if (active()) owner.onError(error);
    } finally {
      if (generation.current === attempt) {
        busyRef.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-foreground-muted">
        Оплата уже начата. Не принимайте деньги повторно. Проверьте эту же операцию.
      </p>
      <p className="text-sm">Получено: {props.pending.request.cash_tendered} TJS</p>
      <Button
        className="w-full"
        disabled={!props.online || props.busy || busy}
        isLoading={busy || props.busy}
        onClick={() => void act(false)}
      >
        Проверить и завершить оплату
      </Button>
      {props.kind === "physical" && (
        <Button
          className="w-full"
          variant="secondary"
          disabled={!props.online || props.busy || busy}
          onClick={() => void act(true)}
        >
          Вернуться к корзине
        </Button>
      )}
    </div>
  );
}
