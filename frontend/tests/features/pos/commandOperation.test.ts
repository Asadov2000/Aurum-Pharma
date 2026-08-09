import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingPosCommand,
  createPendingPosCommand,
  isPosCommandExpired,
  loadPendingPosCommand,
} from "@/features/pos/commandOperation";

const REGISTER_ID = "register-1";
const SALE_ID = "sale-1";

describe("POS command marker", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists a strict PII-free UUIDv4 marker before an item mutation", () => {
    const command = createPendingPosCommand({
      commandType: "item.update",
      registerId: REGISTER_ID,
      saleId: SALE_ID,
      itemId: "item-1",
      qty: "2.5",
    });

    expect(command).toMatchObject({
      version: 1,
      commandType: "item.update",
      registerId: REGISTER_ID,
      saleId: SALE_ID,
      itemId: "item-1",
      qty: "2.5",
      operationId: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      ),
    });
    expect(loadPendingPosCommand(REGISTER_ID)).toEqual(command);
    expect(JSON.stringify(command)).not.toMatch(/patient|phone|email|token|name/i);
  });

  it("refuses a second command and only clears the matching operation", () => {
    const first = createPendingPosCommand({
      commandType: "item.delete",
      registerId: REGISTER_ID,
      saleId: SALE_ID,
      itemId: "item-1",
    });
    expect(first).not.toBeNull();
    expect(
      createPendingPosCommand({ commandType: "sale.create", registerId: REGISTER_ID }),
    ).toBeNull();
    expect(clearPendingPosCommand(REGISTER_ID, "20000000-0000-4000-8000-000000000002")).toBe(false);
    expect(loadPendingPosCommand(REGISTER_ID)).toEqual(first);
    expect(clearPendingPosCommand(REGISTER_ID, first!.operationId)).toBe(true);
  });

  it("rejects unknown fields and garbage-collects malformed records", () => {
    window.localStorage.setItem(
      `pos:pendingCommand:v1:${REGISTER_ID}`,
      JSON.stringify({
        version: 1,
        commandType: "sale.create",
        operationId: "10000000-0000-4000-8000-000000000001",
        registerId: REGISTER_ID,
        createdAt: Date.now(),
        accessToken: "must-not-survive",
      }),
    );

    expect(loadPendingPosCommand(REGISTER_ID)).toBeNull();
    expect(window.localStorage.length).toBe(0);
  });

  it("marks an old unresolved command as expired without deleting it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T10:00:00Z"));
    const command = createPendingPosCommand({
      commandType: "sale.create",
      registerId: REGISTER_ID,
    });
    expect(command).not.toBeNull();

    vi.setSystemTime(new Date(Date.now() + 24 * 60 * 60 * 1_000 + 1));
    expect(isPosCommandExpired(command!)).toBe(true);
    expect(loadPendingPosCommand(REGISTER_ID)).toEqual(command);
    vi.useRealTimers();
  });
});
