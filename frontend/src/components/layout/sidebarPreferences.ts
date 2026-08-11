export type SidebarDesktopMode = "auto" | "compact" | "expanded";

export interface SidebarPreferences {
  desktopMode: SidebarDesktopMode;
  hiddenRoutes: string[];
  favoriteRoutes: string[];
  routeOrder: string[];
}

export interface SidebarSection {
  id: string;
  caption?: string;
  routes: readonly string[];
}

export const SIDEBAR_SECTIONS: readonly SidebarSection[] = [
  { id: "home", routes: ["/"] },
  { id: "launch", caption: "Запуск", routes: ["/onboarding"] },
  { id: "sales", caption: "Продажи", routes: ["/pos", "/sales"] },
  {
    id: "stock",
    caption: "Склад",
    routes: ["/catalog", "/batches", "/incoming", "/suppliers"],
  },
  { id: "analytics", caption: "Аналитика", routes: ["/reports", "/audit"] },
  {
    id: "management",
    caption: "Управление",
    routes: ["/users", "/roles", "/branches", "/registers"],
  },
  {
    id: "system",
    caption: "Система",
    routes: ["/billing", "/notifications", "/security", "/settings"],
  },
  { id: "administration", caption: "Администрирование", routes: ["/admin"] },
];

const STORAGE_PREFIX = "aurum:sidebar:v1";
const MAX_STORED_ROUTES = 64;

export function defaultSidebarPreferences(): SidebarPreferences {
  return {
    desktopMode: "auto",
    hiddenRoutes: [],
    favoriteRoutes: [],
    routeOrder: [],
  };
}

export function sidebarStorageKey(scope: string): string {
  return `${STORAGE_PREFIX}:${encodeURIComponent(scope)}`;
}

function normalizeRoutes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const routes: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (
      typeof item !== "string" ||
      item.length === 0 ||
      item.length > 128 ||
      !item.startsWith("/") ||
      seen.has(item)
    ) {
      continue;
    }
    seen.add(item);
    routes.push(item);
    if (routes.length >= MAX_STORED_ROUTES) break;
  }
  return routes;
}

export function parseSidebarPreferences(value: unknown): SidebarPreferences {
  if (typeof value !== "object" || value === null) return defaultSidebarPreferences();

  const candidate = value as Record<string, unknown>;
  const hiddenRoutes = normalizeRoutes(candidate.hiddenRoutes);
  const hidden = new Set(hiddenRoutes);
  return {
    desktopMode:
      candidate.desktopMode === "compact" || candidate.desktopMode === "expanded"
        ? candidate.desktopMode
        : "auto",
    hiddenRoutes,
    favoriteRoutes: normalizeRoutes(candidate.favoriteRoutes).filter((route) => !hidden.has(route)),
    routeOrder: normalizeRoutes(candidate.routeOrder),
  };
}

export function loadSidebarPreferences(scope: string): SidebarPreferences {
  try {
    const stored = window.localStorage.getItem(sidebarStorageKey(scope));
    return stored === null
      ? defaultSidebarPreferences()
      : parseSidebarPreferences(JSON.parse(stored));
  } catch {
    return defaultSidebarPreferences();
  }
}

export function saveSidebarPreferences(scope: string, preferences: SidebarPreferences): void {
  try {
    window.localStorage.setItem(
      sidebarStorageKey(scope),
      JSON.stringify(parseSidebarPreferences(preferences)),
    );
  } catch {
    // The current session remains usable when the browser blocks preferences.
  }
}

export function orderSidebarItems<T extends { to: string }>(
  items: readonly T[],
  preferences: SidebarPreferences,
): T[] {
  const rank = new Map(preferences.routeOrder.map((route, index) => [route, index]));
  return items
    .map((item, index) => ({ item, index, rank: rank.get(item.to) }))
    .sort((left, right) => {
      if (left.rank === undefined && right.rank === undefined) return left.index - right.index;
      if (left.rank === undefined) return 1;
      if (right.rank === undefined) return -1;
      return left.rank - right.rank;
    })
    .map(({ item }) => item);
}

export function visibleSidebarItems<T extends { to: string }>(
  items: readonly T[],
  preferences: SidebarPreferences,
): T[] {
  const ordered = orderSidebarItems(items, preferences);
  const hidden = new Set(preferences.hiddenRoutes);
  const visible = ordered.filter((item) => !hidden.has(item.to));

  // Corrupted or manually edited storage must not leave the application
  // without navigation. Permissions still define the supplied item set.
  return visible.length > 0 ? visible : ordered.slice(0, 1);
}

export function toggleSidebarRoute(
  preferences: SidebarPreferences,
  route: string,
  availableRoutes: readonly string[],
): SidebarPreferences {
  if (!availableRoutes.includes(route)) return preferences;

  const hidden = new Set(preferences.hiddenRoutes);
  if (hidden.has(route)) {
    hidden.delete(route);
  } else {
    const visibleCount = availableRoutes.filter((item) => !hidden.has(item)).length;
    if (visibleCount <= 1) return preferences;
    hidden.add(route);
  }

  return {
    ...preferences,
    hiddenRoutes: [...hidden],
    favoriteRoutes: preferences.favoriteRoutes.filter((item) => !hidden.has(item)),
  };
}

export function toggleSidebarFavorite(
  preferences: SidebarPreferences,
  route: string,
  availableRoutes: readonly string[],
): SidebarPreferences {
  if (!availableRoutes.includes(route) || preferences.hiddenRoutes.includes(route)) {
    return preferences;
  }

  const favorites = new Set(preferences.favoriteRoutes);
  if (favorites.has(route)) favorites.delete(route);
  else favorites.add(route);
  return { ...preferences, favoriteRoutes: [...favorites] };
}

export function moveSidebarRoute(
  preferences: SidebarPreferences,
  route: string,
  direction: -1 | 1,
  availableRoutes: readonly string[],
  sectionRoutes: readonly string[],
): SidebarPreferences {
  const available = new Set(availableRoutes);
  const section = new Set(sectionRoutes);
  const ordered = orderSidebarItems(
    availableRoutes.map((to) => ({ to })),
    preferences,
  ).map(({ to }) => to);
  const orderedSection = ordered.filter((item) => section.has(item));
  const index = orderedSection.indexOf(route);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= orderedSection.length) return preferences;

  [orderedSection[index], orderedSection[target]] = [
    orderedSection[target]!,
    orderedSection[index]!,
  ];
  let sectionIndex = 0;
  const nextAccessibleOrder = ordered.map((item) =>
    section.has(item) ? orderedSection[sectionIndex++]! : item,
  );
  const unavailableStoredRoutes = preferences.routeOrder.filter((item) => !available.has(item));
  return { ...preferences, routeOrder: [...nextAccessibleOrder, ...unavailableStoredRoutes] };
}

export function sidebarSectionForRoute(route: string): SidebarSection | undefined {
  return SIDEBAR_SECTIONS.find((section) => section.routes.includes(route));
}
