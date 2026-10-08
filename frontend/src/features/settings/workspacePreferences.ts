import { type WorkspacePreferences } from "./types";

export function parseWorkspace(value: unknown): WorkspacePreferences | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (
    record.desktop_mode !== "auto" &&
    record.desktop_mode !== "compact" &&
    record.desktop_mode !== "expanded"
  )
    return null;
  const hidden = routes(record.hidden_routes);
  const favorites = routes(record.favorite_routes);
  const order = routes(record.route_order);
  if (
    !hidden ||
    !favorites ||
    !order ||
    typeof record.start_route !== "string" ||
    record.start_route.length > 128
  )
    return null;
  return {
    desktop_mode: record.desktop_mode,
    hidden_routes: hidden,
    favorite_routes: favorites,
    route_order: order,
    start_route: record.start_route,
  };
}

function routes(value: unknown): string[] | null {
  return Array.isArray(value) &&
    value.length <= 64 &&
    value.every((item) => typeof item === "string")
    ? value
    : null;
}
