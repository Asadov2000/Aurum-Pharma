import { createOperationId } from "@/lib/operationId";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("createOperationId", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("creates a UUID v4 suitable for idempotent operations", () => {
    expect(createOperationId()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("delegates to native randomUUID without requesting fallback entropy", () => {
    const expected = "01234567-89ab-4cde-8f01-23456789abcd";
    const randomUUID = vi.fn(() => expected);
    const getRandomValues = vi.fn();
    vi.stubGlobal("crypto", { randomUUID, getRandomValues });

    expect(createOperationId()).toBe(expected);
    expect(randomUUID).toHaveBeenCalledExactlyOnceWith();
    expect(getRandomValues).not.toHaveBeenCalled();
  });

  it.each([
    { bits: 0x00, expected: "00010203-0405-4007-8009-0a0b0c0d0e0f" },
    { bits: 0x55, expected: "00010203-0405-4507-9509-0a0b0c0d0e0f" },
    { bits: 0xaa, expected: "00010203-0405-4a07-aa09-0a0b0c0d0e0f" },
    { bits: 0xff, expected: "00010203-0405-4f07-bf09-0a0b0c0d0e0f" },
  ])("formats secure fallback bytes as UUID v4 with variant bits $bits", ({ bits, expected }) => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set([0, 1, 2, 3, 4, 5, bits, 7, bits, 9, 10, 11, 12, 13, 14, 15]);
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });

    expect(createOperationId()).toBe(expected);
    expect(getRandomValues).toHaveBeenCalledExactlyOnceWith(expect.any(Uint8Array));
    expect(getRandomValues.mock.calls[0]?.[0]).toHaveLength(16);
  });

  it("requests fresh secure entropy for each fallback operation", () => {
    let callCount = 0;
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.fill(callCount++ === 0 ? 0x00 : 0xff);
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });

    expect(createOperationId()).toBe("00000000-0000-4000-8000-000000000000");
    expect(createOperationId()).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(getRandomValues).toHaveBeenCalledTimes(2);
    expect(getRandomValues.mock.calls[0]?.[0]).not.toBe(getRandomValues.mock.calls[1]?.[0]);
  });

  it("fails closed when neither secure source is available", () => {
    vi.stubGlobal("crypto", {});
    const insecureRandom = vi.spyOn(Math, "random");

    expect(() => createOperationId()).toThrow("Безопасный генератор кода операции недоступен");
    expect(insecureRandom).not.toHaveBeenCalled();
  });
});
