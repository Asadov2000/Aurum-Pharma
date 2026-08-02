import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  defaultSidebarPreferences,
  loadSidebarPreferences,
  moveSidebarRoute,
  parseSidebarPreferences,
  saveSidebarPreferences,
  sidebarStorageKey,
  toggleSidebarFavorite,
  toggleSidebarRoute,
  visibleSidebarItems,
} from "@/components/layout/sidebarPreferences";

const ITEMS = [
  { to: "/pos", label: "Касса" },
  { to: "/sales", label: "Чеки" },
  { to: "/catalog", label: "Каталог" },
] as const;

describe("sidebar preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("uses automatic layout for missing or damaged preferences", () => {
    expect(loadSidebarPreferences("user-1:tenant-1")).toEqual(defaultSidebarPreferences());

    window.localStorage.setItem(sidebarStorageKey("user-1:tenant-1"), "{broken");
    expect(loadSidebarPreferences("user-1:tenant-1")).toEqual(defaultSidebarPreferences());
    expect(parseSidebarPreferences(null)).toEqual(defaultSidebarPreferences());
  });

  it("normalizes route arrays and removes hidden routes from favorites", () => {
    expect(
      parseSidebarPreferences({
        desktopMode: "compact",
        hiddenRoutes: ["/sales", "/sales", 42, "not-a-route"],
        favoriteRoutes: ["/sales", "/pos", "/pos"],
        routeOrder: ["/catalog", "/pos", "/catalog"],
      }),
    ).toEqual({
      desktopMode: "compact",
      hiddenRoutes: ["/sales"],
      favoriteRoutes: ["/pos"],
      routeOrder: ["/catalog", "/pos"],
    });
  });

  it("keeps settings separate by account and tenant", () => {
    saveSidebarPreferences("user-1:tenant-1", {
      ...defaultSidebarPreferences(),
      favoriteRoutes: ["/pos"],
    });
    saveSidebarPreferences("user-1:tenant-2", {
      ...defaultSidebarPreferences(),
      hiddenRoutes: ["/sales"],
    });

    expect(loadSidebarPreferences("user-1:tenant-1").favoriteRoutes).toEqual(["/pos"]);
    expect(loadSidebarPreferences("user-1:tenant-2").hiddenRoutes).toEqual(["/sales"]);
  });

  it("continues in memory when storage writes are blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    expect(() =>
      saveSidebarPreferences("user-1:tenant-1", {
        ...defaultSidebarPreferences(),
        desktopMode: "expanded",
      }),
    ).not.toThrow();
  });

  it("never creates links outside the permission-filtered item set", () => {
    const preferences = parseSidebarPreferences({
      desktopMode: "expanded",
      hiddenRoutes: [],
      favoriteRoutes: ["/admin/tenants"],
      routeOrder: ["/admin/tenants", "/catalog", "/pos"],
    });

    expect(visibleSidebarItems(ITEMS, preferences).map((item) => item.to)).toEqual([
      "/catalog",
      "/pos",
      "/sales",
    ]);
  });

  it("keeps one allowed route visible when storage hides everything", () => {
    const preferences = {
      ...defaultSidebarPreferences(),
      hiddenRoutes: ITEMS.map((item) => item.to),
    };

    expect(visibleSidebarItems(ITEMS, preferences).map((item) => item.to)).toEqual(["/pos"]);
  });

  it("prevents hiding the final route and removes hidden favorites", () => {
    let preferences = {
      ...defaultSidebarPreferences(),
      hiddenRoutes: ["/sales", "/catalog"],
      favoriteRoutes: ["/pos"],
    };
    preferences = toggleSidebarRoute(
      preferences,
      "/pos",
      ITEMS.map((item) => item.to),
    );
    expect(preferences.hiddenRoutes).not.toContain("/pos");

    preferences = toggleSidebarRoute(
      { ...preferences, hiddenRoutes: ["/catalog"], favoriteRoutes: ["/sales"] },
      "/sales",
      ITEMS.map((item) => item.to),
    );
    expect(preferences.hiddenRoutes).toContain("/sales");
    expect(preferences.favoriteRoutes).not.toContain("/sales");
  });

  it("favorites visible routes and reorders only within a section", () => {
    let preferences = toggleSidebarFavorite(
      defaultSidebarPreferences(),
      "/sales",
      ITEMS.map((item) => item.to),
    );
    expect(preferences.favoriteRoutes).toEqual(["/sales"]);

    preferences = moveSidebarRoute(
      preferences,
      "/sales",
      -1,
      ITEMS.map((item) => item.to),
      ["/pos", "/sales"],
    );
    expect(preferences.routeOrder).toEqual(["/sales", "/pos", "/catalog"]);
  });
});
