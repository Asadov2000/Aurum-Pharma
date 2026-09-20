import { type DesktopBridgeTarget } from "@/lib/desktopBridge";

export type DataPlane =
  | { readonly mode: "cloud" }
  | { readonly mode: "edge-readonly"; readonly branchId: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function resolveDataPlane(target: DesktopBridgeTarget = window): DataPlane {
  const configured = target.aurumDesktop?.dataPlane;
  if (configured?.mode === "edge-readonly" && UUID_PATTERN.test(configured.branchId.trim())) {
    return { mode: "edge-readonly", branchId: configured.branchId.toLowerCase() };
  }
  return { mode: "cloud" };
}
