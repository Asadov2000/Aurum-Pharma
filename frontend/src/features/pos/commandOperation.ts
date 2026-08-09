import { generateUuidV4, isUuidV4 } from "./operationId";
import { readStoredJson, removeStoredValue, writeStoredJson } from "./operationStorage";

const STORAGE_PREFIX = "pos:pendingCommand:v1:";
const ENTITY_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const QTY_PATTERN = /^(?:0|[1-9]\d{0,8})(?:\.\d{1,3})?$/;

const POS_COMMAND_TTL_MS = 24 * 60 * 60 * 1_000;
export type PosCommandType = "sale.create" | "item.add" | "item.update" | "item.delete";

interface CommandBase {
  version: 1;
  commandType: PosCommandType;
  operationId: string;
  registerId: string;
  createdAt: number;
}

export interface CreateSaleCommand extends CommandBase {
  commandType: "sale.create";
}

export interface AddSaleItemCommand extends CommandBase {
  commandType: "item.add";
  saleId: string;
  catalogId: string;
  qty: string;
  expiredSaleConfirmed: boolean;
}

export interface UpdateSaleItemCommand extends CommandBase {
  commandType: "item.update";
  saleId: string;
  itemId: string;
  qty: string;
}

export interface DeleteSaleItemCommand extends CommandBase {
  commandType: "item.delete";
  saleId: string;
  itemId: string;
}

export type PendingPosCommand =
  | CreateSaleCommand
  | AddSaleItemCommand
  | UpdateSaleItemCommand
  | DeleteSaleItemCommand;
export type NewPosCommand = PendingPosCommand extends infer Command
  ? Command extends PendingPosCommand
    ? Omit<Command, "version" | "operationId" | "createdAt">
    : never
  : never;

const commandKey = (registerId: string): string => `${STORAGE_PREFIX}${registerId}`;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEntityId(value: unknown): value is string {
  return typeof value === "string" && ENTITY_ID_PATTERN.test(value);
}

function isQty(value: unknown): value is string {
  return typeof value === "string" && QTY_PATTERN.test(value) && Number(value) > 0;
}

function isPendingCommand(value: unknown, registerId: string): value is PendingPosCommand {
  if (
    !isRecord(value) ||
    value.version !== 1 ||
    !isUuidV4(value.operationId) ||
    value.registerId !== registerId ||
    !isEntityId(value.registerId) ||
    typeof value.createdAt !== "number" ||
    !Number.isSafeInteger(value.createdAt) ||
    value.createdAt <= 0 ||
    value.createdAt > Date.now() + 5 * 60_000
  ) {
    return false;
  }
  const keyCount = Object.keys(value).length;
  if (value.commandType === "sale.create") return keyCount === 5;
  if (value.commandType === "item.add") {
    return (
      keyCount === 9 &&
      isEntityId(value.saleId) &&
      isEntityId(value.catalogId) &&
      isQty(value.qty) &&
      typeof value.expiredSaleConfirmed === "boolean"
    );
  }
  if (value.commandType === "item.update") {
    return (
      keyCount === 8 && isEntityId(value.saleId) && isEntityId(value.itemId) && isQty(value.qty)
    );
  }
  return (
    keyCount === 7 &&
    value.commandType === "item.delete" &&
    isEntityId(value.saleId) &&
    isEntityId(value.itemId)
  );
}

export function loadPendingPosCommand(registerId: string): PendingPosCommand | null {
  if (!isEntityId(registerId)) return null;
  const parsed = readStoredJson(commandKey(registerId));
  if (isPendingCommand(parsed, registerId)) return parsed;
  if (parsed !== null) removeStoredValue(commandKey(registerId));
  return null;
}

export function createPendingPosCommand(input: NewPosCommand): PendingPosCommand | null {
  if (loadPendingPosCommand(input.registerId) !== null) return null;
  const operation = {
    ...input,
    version: 1,
    operationId: generateUuidV4(),
    createdAt: Date.now(),
  } as PendingPosCommand;
  if (!isPendingCommand(operation, input.registerId)) return null;

  return writeStoredJson(commandKey(input.registerId), operation) ? operation : null;
}

export function clearPendingPosCommand(registerId: string, operationId: string): boolean {
  if (loadPendingPosCommand(registerId)?.operationId !== operationId) return false;
  return removeStoredValue(commandKey(registerId));
}

export function isPosCommandExpired(command: PendingPosCommand, now = Date.now()): boolean {
  return now - command.createdAt > POS_COMMAND_TTL_MS;
}
