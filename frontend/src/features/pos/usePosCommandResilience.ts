import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  addSaleItem,
  createSale,
  deleteSaleItem,
  getPosCommandResult,
  getSale,
  updateSaleItem,
} from "./api";
import {
  clearPendingPosCommand,
  createPendingPosCommand,
  isPosCommandExpired,
  loadPendingPosCommand,
  type NewPosCommand,
  type PendingPosCommand,
} from "./commandOperation";
import { posKeys } from "./queries";
import { type PosCommandResult, type SaleDetails } from "./types";

type CommandPhase = "idle" | "working" | "retry" | "collision" | "expired";

export interface AppliedPosCommand {
  command: PendingPosCommand;
  result: unknown;
  sale: SaleDetails;
}

interface CommandRunResult {
  applied: AppliedPosCommand | null;
  rejectedError?: unknown;
}

interface UsePosCommandResilienceOptions {
  registerId: string;
  onApplied: (applied: AppliedPosCommand) => void;
}

interface PosCommandResilience {
  blocked: boolean;
  isWorking: boolean;
  message: string | null;
  canRetry: boolean;
  begin: (input: NewPosCommand) => Promise<CommandRunResult>;
  retry: () => Promise<void>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNotFound(error: unknown): boolean {
  return isAxiosError(error) && error.response?.status === 404;
}

function isCollision(error: unknown): boolean {
  return isAxiosError(error) && error.response?.status === 409;
}

function isDefiniteBusinessRejection(error: unknown): boolean {
  if (!isAxiosError(error) || error.response === undefined) return false;
  const status = error.response.status;
  return status >= 400 && status < 500 && status !== 408 && status !== 409;
}

function savedResultMatches(command: PendingPosCommand, result: unknown): boolean {
  if (!isRecord(result)) return false;
  if (command.commandType === "sale.create") {
    return result.register_id === command.registerId && typeof result.id === "string";
  }
  if (command.commandType === "item.update") {
    return (
      result.id === command.itemId &&
      result.sale_id === command.saleId &&
      typeof result.qty === "string" &&
      Number(result.qty) === Number(command.qty)
    );
  }
  if (command.commandType === "item.delete") {
    return (
      result.command_type === "item.delete" &&
      result.sale_id === command.saleId &&
      result.item_id === command.itemId &&
      result.status === "deleted"
    );
  }
  if (!Array.isArray(result.items) || typeof result.requires_prescription_log !== "boolean") {
    return false;
  }
  const matchingItems = result.items.filter(
    (item) =>
      isRecord(item) && item.sale_id === command.saleId && item.catalog_id === command.catalogId,
  );
  const totalQty = matchingItems.reduce(
    (sum, item) => sum + (isRecord(item) && typeof item.qty === "string" ? Number(item.qty) : 0),
    0,
  );
  return matchingItems.length > 0 && Math.abs(totalQty - Number(command.qty)) < 0.0005;
}

function saleIdFrom(command: PendingPosCommand, result: unknown): string | null {
  if (command.commandType !== "sale.create") return command.saleId;
  return isRecord(result) && typeof result.id === "string" ? result.id : null;
}

function unwrapStoredResult(command: PendingPosCommand, stored: PosCommandResult): unknown | null {
  if (stored.operation_id !== command.operationId) return null;
  if (command.commandType !== "sale.create" && stored.sale_id !== command.saleId) return null;
  const result = stored.result;
  if (result.command_type !== command.commandType) return null;
  switch (result.command_type) {
    case "sale.create":
      return result.sale;
    case "item.add":
      return result.item_add;
    case "item.update":
      return result.item;
    case "item.delete":
      return result;
  }
}

async function sendCommand(command: PendingPosCommand): Promise<unknown> {
  switch (command.commandType) {
    case "sale.create":
      return createSale(command.registerId, command.operationId);
    case "item.add":
      return addSaleItem(
        command.saleId,
        command.catalogId,
        command.qty,
        command.operationId,
        command.expiredSaleConfirmed,
      );
    case "item.update":
      return updateSaleItem(command.saleId, command.itemId, command.qty, command.operationId);
    case "item.delete":
      return deleteSaleItem(command.saleId, command.itemId, command.operationId);
  }
}

export function usePosCommandResilience({
  registerId,
  onApplied,
}: UsePosCommandResilienceOptions): PosCommandResilience {
  const queryClient = useQueryClient();
  const initialCommandRef = useRef<PendingPosCommand | null>(null);
  if (initialCommandRef.current === null) {
    initialCommandRef.current = loadPendingPosCommand(registerId);
  }
  const [pending, setPending] = useState<PendingPosCommand | null>(initialCommandRef.current);
  const [phase, setPhase] = useState<CommandPhase>(pending ? "working" : "idle");
  const pendingRef = useRef(pending);
  const recoveryStartedRef = useRef(false);
  const runningRef = useRef(false);
  const onAppliedRef = useRef(onApplied);
  pendingRef.current = pending;
  onAppliedRef.current = onApplied;

  const markResolved = useCallback(
    async (command: PendingPosCommand, result: unknown): Promise<AppliedPosCommand | null> => {
      if (!savedResultMatches(command, result)) {
        setPhase("collision");
        return null;
      }
      const saleId = saleIdFrom(command, result);
      if (!saleId) {
        setPhase("collision");
        return null;
      }
      const canonical = await getSale(saleId);
      if (canonical.id !== saleId || canonical.register_id !== command.registerId) {
        setPhase("collision");
        return null;
      }
      if (
        command.commandType === "item.delete" &&
        canonical.items.some((item) => item.id === command.itemId)
      ) {
        setPhase("collision");
        return null;
      }
      queryClient.setQueryData(posKeys.sale(saleId), canonical);
      const applied = { command, result, sale: canonical };
      onAppliedRef.current(applied);
      if (!clearPendingPosCommand(registerId, command.operationId)) {
        setPhase("retry");
        return null;
      }
      pendingRef.current = null;
      setPending(null);
      setPhase("idle");
      return applied;
    },
    [queryClient, registerId],
  );

  const reconcile = useCallback(
    async (command: PendingPosCommand, allowReplay: boolean): Promise<CommandRunResult> => {
      if (runningRef.current) return { applied: null };
      runningRef.current = true;
      setPhase("working");
      try {
        let stored: PosCommandResult;
        try {
          stored = await getPosCommandResult(command.operationId);
        } catch (error) {
          if (!isNotFound(error)) {
            setPhase("retry");
            return { applied: null };
          }
          if (!allowReplay || isPosCommandExpired(command)) {
            setPhase(isPosCommandExpired(command) ? "expired" : "retry");
            return { applied: null };
          }
          let replayResult: unknown;
          try {
            replayResult = await sendCommand(command);
          } catch (replayError) {
            if (isCollision(replayError)) {
              setPhase("collision");
              return { applied: null };
            }
            if (isDefiniteBusinessRejection(replayError)) {
              clearPendingPosCommand(registerId, command.operationId);
              pendingRef.current = null;
              setPending(null);
              setPhase("idle");
              return { applied: null, rejectedError: replayError };
            }
            setPhase("retry");
            return { applied: null };
          }
          try {
            return { applied: await markResolved(command, replayResult) };
          } catch {
            setPhase("retry");
            return { applied: null };
          }
        }
        const storedResult = unwrapStoredResult(command, stored);
        if (storedResult === null) {
          setPhase("collision");
          return { applied: null };
        }
        return { applied: await markResolved(command, storedResult) };
      } catch {
        setPhase("retry");
        return { applied: null };
      } finally {
        runningRef.current = false;
      }
    },
    [markResolved, registerId],
  );

  const begin = useCallback(
    async (input: NewPosCommand): Promise<CommandRunResult> => {
      if (pendingRef.current || runningRef.current) return { applied: null };
      const command = createPendingPosCommand(input);
      if (!command) {
        setPhase("idle");
        return {
          applied: null,
          rejectedError: new Error("Не удалось сохранить безопасный номер команды"),
        };
      }
      pendingRef.current = command;
      setPending(command);
      runningRef.current = true;
      setPhase("working");
      let result: unknown;
      try {
        result = await sendCommand(command);
      } catch (error) {
        if (isCollision(error)) {
          runningRef.current = false;
          setPhase("collision");
          return { applied: null };
        }
        if (isDefiniteBusinessRejection(error)) {
          clearPendingPosCommand(registerId, command.operationId);
          pendingRef.current = null;
          setPending(null);
          setPhase("idle");
          runningRef.current = false;
          return { applied: null, rejectedError: error };
        }
        runningRef.current = false;
        return reconcile(command, false);
      }
      try {
        return { applied: await markResolved(command, result) };
      } catch {
        setPhase("retry");
        return { applied: null };
      } finally {
        runningRef.current = false;
      }
    },
    [markResolved, reconcile, registerId],
  );

  const retry = useCallback(async (): Promise<void> => {
    const command = pendingRef.current;
    if (!command || phase === "collision") return;
    await reconcile(command, true);
  }, [phase, reconcile]);

  useEffect(() => {
    if (recoveryStartedRef.current) return;
    recoveryStartedRef.current = true;
    const command = initialCommandRef.current;
    if (command) void reconcile(command, !isPosCommandExpired(command));
  }, [reconcile]);

  const message =
    phase === "working"
      ? "Сверяем последнюю команду с сервером…"
      : phase === "retry"
        ? "Команда не подтверждена. Проверьте связь и повторите."
        : phase === "collision"
          ? "Конфликт номера операции. Работа с чеком остановлена — обратитесь к администратору."
          : phase === "expired"
            ? "Команда устарела и требует ручной сверки администратором."
            : null;

  return {
    blocked: pending !== null || phase !== "idle",
    isWorking: phase === "working",
    message,
    canRetry: pending !== null && phase === "retry",
    begin,
    retry,
  };
}
