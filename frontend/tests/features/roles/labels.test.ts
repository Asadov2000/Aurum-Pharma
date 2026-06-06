import { describe, expect, it } from "vitest";

import {
  allowedRoleLevels,
  currentUserLevel,
  groupLabel,
  levelLabel,
} from "@/features/roles/labels";

describe("roles labels + level helpers", () => {
  it("levelLabel matches the real tiers (1 dev … 4 cashier)", () => {
    expect(levelLabel(1)).toBe("Разработчик");
    expect(levelLabel(2)).toBe("Администратор");
    expect(levelLabel(3)).toBe("Владелец");
    expect(levelLabel(4)).toBe("Кассир");
  });

  it("groupLabel translates group codes to Russian section titles", () => {
    expect(groupLabel("pos")).toBe("Касса");
    expect(groupLabel("tenant")).toBe("Аптека");
    expect(groupLabel("users")).toBe("Сотрудники");
  });

  it("currentUserLevel mirrors the backend CurrentUser.level", () => {
    expect(currentUserLevel({ is_developer: true })).toBe(1);
    expect(currentUserLevel({ is_administrator: true })).toBe(2);
    expect(currentUserLevel({ permissions: ["users.invite"] })).toBe(3);
    expect(currentUserLevel({ permissions: ["roles.assign"] })).toBe(3);
    expect(currentUserLevel({ permissions: ["pos.sell"] })).toBe(4);
    expect(currentUserLevel(null)).toBe(4);
  });

  it("allowedRoleLevels are strictly weaker than the actor", () => {
    expect(allowedRoleLevels(3)).toEqual([4]); // owner → cashier-tier only
    expect(allowedRoleLevels(2)).toEqual([3, 4]);
    expect(allowedRoleLevels(1)).toEqual([2, 3, 4]);
    expect(allowedRoleLevels(4)).toEqual([]);
  });
});
