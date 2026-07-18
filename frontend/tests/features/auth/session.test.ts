import { beforeEach, describe, expect, it } from "vitest";

import { clearClientSession } from "@/features/auth/session";
import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";

describe("clearClientSession", () => {
  beforeEach(() => {
    queryClient.clear();
    useAuthStore.getState().clear();
  });

  it("removes cached tenant data together with the auth state", () => {
    useAuthStore.getState().setTokens({
      access_token: "tenant-a-access",
      token_type: "bearer",
      expires_in: 900,
    });
    queryClient.setQueryData(["catalog", "list"], [{ id: "tenant-a-item" }]);

    clearClientSession();

    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(queryClient.getQueryData(["catalog", "list"])).toBeUndefined();
  });
});
