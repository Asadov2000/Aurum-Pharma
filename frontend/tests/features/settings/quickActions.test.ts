import { describe, expect, it } from "vitest";

import {
  applyQuickActions,
  defaultQuickActionPreferences,
  parseQuickActionMenuPreferences,
  parseQuickActionPreferences,
  QUICK_ACTION_SECTIONS,
} from "@/features/settings/quickActions";

describe("quick action preferences", () => {
  it("validates the stored shape and removes unknown or repeated command IDs", () => {
    expect(
      parseQuickActionMenuPreferences(
        { enabled: true, order: ["copy_name", "secret", "copy_name"], hidden: ["edit", "secret"] },
        "catalog",
      ),
    ).toEqual({
      enabled: true,
      order: ["copy_name", "open", "edit", "copy_barcode", "restore", "archive"],
      hidden: ["edit"],
    });
    expect(
      parseQuickActionMenuPreferences(
        { ...defaultQuickActionPreferences("catalog"), access_token: "forbidden" },
        "catalog",
      ),
    ).toBeNull();
    expect(
      parseQuickActionMenuPreferences({ enabled: "yes", order: [], hidden: [] }, "batches"),
    ).toBeNull();
    expect(parseQuickActionPreferences({ unknown: {} })).toBeNull();
    expect(parseQuickActionPreferences({})).toBeNull();
    expect(
      parseQuickActionPreferences({ batches: defaultQuickActionPreferences("batches") }),
    ).toEqual({ batches: defaultQuickActionPreferences("batches") });
  });

  it("rejects configurations that hide every supported command", () => {
    for (const section of QUICK_ACTION_SECTIONS) {
      const defaults = defaultQuickActionPreferences(section);
      expect(
        parseQuickActionMenuPreferences({ ...defaults, hidden: defaults.order }, section),
      ).toBeNull();
      expect(
        parseQuickActionMenuPreferences(
          { ...defaults, enabled: false, hidden: [...defaults.order, "unknown"] },
          section,
        ),
      ).toBeNull();
    }
  });

  it("keeps extended sections independent and dangerous employee commands at the end", () => {
    const incoming = defaultQuickActionPreferences("incoming");
    const employees = defaultQuickActionPreferences("employees");
    const saved = parseQuickActionPreferences({
      incoming: { ...incoming, hidden: ["copy_number"] },
      employees,
    });
    expect(saved?.incoming?.hidden).toEqual(["copy_number"]);
    expect(saved?.employees?.hidden).toEqual([]);
    expect(
      parseQuickActionMenuPreferences(
        { ...employees, order: ["offboard", "transfer_ownership", "open"] },
        "employees",
      )?.order.slice(-4),
    ).toEqual(["offboard", "transfer_ownership", "revoke_sessions", "suspend"]);
    // A cached custom menu must never turn a document command into a staff permission.
    expect(
      parseQuickActionMenuPreferences({ ...incoming, order: ["offboard"] }, "incoming")?.order,
    ).toEqual(["open", "copy_number"]);
  });

  it("orders only the currently allowed commands, appends new ones, and keeps danger last", () => {
    const commands = [
      { id: "archive", tone: "danger" as const },
      { id: "open" },
      { id: "copy_name" },
      { id: "new_command" },
    ];
    const config = {
      enabled: false,
      order: ["archive", "edit", "copy_name", "open"],
      hidden: ["open"],
    };
    expect(applyQuickActions(commands, config).map((item) => item.id)).toEqual([
      "copy_name",
      "new_command",
      "archive",
    ]);
    expect(commands.map((item) => item.id)).toEqual([
      "archive",
      "open",
      "copy_name",
      "new_command",
    ]);
  });
});
