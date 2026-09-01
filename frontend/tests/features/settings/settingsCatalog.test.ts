import { describe, expect, it } from "vitest";

import {
  parseSettingsSearch,
  settingsCategories,
} from "@/features/settings/search";

describe("settings catalog", () => {
  it("keeps one canonical definition for every settings section", () => {
    const ids = settingsCategories.map((category) => category.id);

    expect(new Set(ids).size).toBe(ids.length);
  });

  it("assigns each section to the expected storage boundary", () => {
    for (const category of settingsCategories) {
      if (category.group === "device") {
        expect(category.source).toBe("device_preferences");
      } else if (category.group === "owner") {
        expect(category.source).toBe("tenant_settings");
      } else {
        expect(category.source).not.toBe("tenant_settings");
        expect(category.source).not.toBe("device_preferences");
      }
    }
  });

  it("rejects unknown deep links instead of opening an arbitrary section", () => {
    expect(parseSettingsSearch({ section: "platform-secrets" })).toEqual({});
    expect(parseSettingsSearch({ section: "sales" })).toEqual({ section: "sales" });
  });
});
