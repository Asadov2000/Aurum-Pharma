import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getPhysicalCartOptions } from "@/features/pos/physicalCartOptions";
import { api, configureAuthHooks } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos } from "./posIdentityFixture";

const defaultAdapter = api.defaults.adapter;

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("physical cart options authority", () => {
  beforeEach(() => {
    authenticatePos();
    useAuthStore.setState({ accessToken: "synthetic-access" });
    configureAuthHooks({
      getAccessToken: () => useAuthStore.getState().accessToken,
      refreshTokens: async () => null,
      requestMfaStepUp: async () => null,
      onAuthFailure: () => {},
    });
  });

  afterEach(() => {
    api.defaults.adapter = defaultAdapter;
    configureAuthHooks({
      getAccessToken: () => null,
      refreshTokens: async () => null,
      requestMfaStepUp: async () => null,
      onAuthFailure: () => {},
    });
    useAuthStore.getState().clear();
  });

  it.each(["account", "tenant", "logout"])(
    "rejects delayed options after %s changes instead of exposing the prior stock",
    async (change) => {
      const started = deferred();
      const released = deferred();
      api.defaults.adapter = async (config) => {
        started.resolve();
        await released.promise;
        return {
          data: { stock: { base_units: 99 } },
          status: 200,
          statusText: "OK",
          headers: {},
          config,
        };
      };
      const pending = getPhysicalCartOptions("sale-1", "catalog-1");
      const rejected = expect(pending).rejects.toThrow("Authentication session changed");
      await started.promise;
      if (change === "logout") useAuthStore.getState().clear();
      else
        authenticatePos({
          userId: change === "account" ? "other" : "u-1",
          tenantId: change === "tenant" ? "other" : "t-1",
        });
      released.resolve();
      await rejected;
    },
  );

  it("allows the same authority to rotate its token during the request", async () => {
    const started = deferred();
    const released = deferred();
    const data = { retail_unit_price: "0.01", intact_blisters: null, checkout_enabled: false };
    api.defaults.adapter = async (config) => {
      started.resolve();
      await released.promise;
      return { data, status: 200, statusText: "OK", headers: {}, config };
    };
    const pending = getPhysicalCartOptions("sale-1", "catalog-1");
    await started.promise;
    useAuthStore.setState({ accessToken: "synthetic-rotated" });
    released.resolve();
    expect(await pending).toBe(data);
  });
});
