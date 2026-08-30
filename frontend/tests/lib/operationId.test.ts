import { createOperationId } from "@/lib/operationId";
import { describe, expect, it } from "vitest";

describe("createOperationId", () => {
  it("creates a UUID v4 suitable for idempotent operations", () => {
    expect(createOperationId()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});
