import { beforeEach, describe, expect, it } from "vitest";

import {
  cancelMfaStepUp,
  completeMfaStepUp,
  requestMfaStepUp,
} from "@/features/auth/stepUpCoordinator";

describe("MFA step-up coordinator", () => {
  beforeEach(() => {
    cancelMfaStepUp();
  });

  it("shares a pending confirmation and resolves it with the token", async () => {
    const first = requestMfaStepUp();
    const second = requestMfaStepUp();

    expect(second).toBe(first);
    completeMfaStepUp("new-access");

    await expect(first).resolves.toBe("new-access");
  });

  it("resolves cancellation without an access token", async () => {
    const pending = requestMfaStepUp();

    cancelMfaStepUp();

    await expect(pending).resolves.toBeNull();
  });
});
