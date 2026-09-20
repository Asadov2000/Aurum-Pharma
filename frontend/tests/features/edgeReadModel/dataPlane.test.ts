import { describe, expect, it } from "vitest";

import { resolveDataPlane } from "@/features/edgeReadModel/dataPlane";
import { type DesktopBridgeTarget } from "@/lib/desktopBridge";

describe("resolveDataPlane", () => {
  it("keeps browsers and unconfigured desktop hosts on Cloud", () => {
    expect(resolveDataPlane({})).toEqual({ mode: "cloud" });
    expect(resolveDataPlane({ aurumDesktop: { dataPlane: { mode: "cloud" } } })).toEqual({
      mode: "cloud",
    });
  });

  it("uses the explicitly bound Edge branch", () => {
    const target: DesktopBridgeTarget = {
      aurumDesktop: {
        dataPlane: {
          mode: "edge-readonly",
          branchId: "20000000-0000-4000-8000-000000000002",
        },
      },
    };

    expect(resolveDataPlane(target)).toEqual({
      mode: "edge-readonly",
      branchId: "20000000-0000-4000-8000-000000000002",
    });
  });

  it("fails closed to Cloud for an invalid branch binding", () => {
    expect(
      resolveDataPlane({
        aurumDesktop: {
          dataPlane: { mode: "edge-readonly", branchId: "not-a-uuid" },
        },
      }),
    ).toEqual({ mode: "cloud" });
  });
});
