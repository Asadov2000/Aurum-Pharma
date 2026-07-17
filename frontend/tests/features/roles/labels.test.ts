import { describe, expect, it } from "vitest";

import { groupLabel } from "@/features/roles/labels";

describe("roles labels", () => {
  it("translates permission group codes to Russian section titles", () => {
    expect(groupLabel("pos")).toBe("Касса");
    expect(groupLabel("tenant")).toBe("Аптека");
    expect(groupLabel("users")).toBe("Сотрудники");
  });

  it("keeps an unknown server group readable", () => {
    expect(groupLabel("new_group")).toBe("new_group");
  });
});
