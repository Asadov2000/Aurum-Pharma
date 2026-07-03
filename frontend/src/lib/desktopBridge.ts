export const DESKTOP_BRIDGE_GLOBAL = "aurumDesktop";

export type DesktopBridgeCapability =
  | "barcode-scanner"
  | "cash-drawer"
  | "file-export"
  | "receipt-print";

export type DesktopBridgeMessage =
  | {
      readonly type: "aurum.desktop.ready";
      readonly payload?: undefined;
    }
  | {
      readonly type: "aurum.receipt.print";
      readonly payload: {
        readonly saleId: string;
      };
    };

export interface AurumDesktopBridge {
  readonly appVersion?: string;
  readonly capabilities?: readonly DesktopBridgeCapability[];
  readonly platform?: "windows";
  postMessage?(message: DesktopBridgeMessage): void;
}

export interface DesktopWebViewBridge {
  postMessage?(message: DesktopBridgeMessage): void;
}

export interface DesktopBridgeTarget {
  readonly aurumDesktop?: AurumDesktopBridge;
  readonly chrome?: {
    readonly webview?: DesktopWebViewBridge;
  };
}

export interface DesktopBridgeInfo {
  readonly appVersion: string | null;
  readonly capabilities: readonly DesktopBridgeCapability[];
  readonly isAvailable: boolean;
  readonly platform: "windows" | null;
}

declare global {
  interface Window {
    readonly aurumDesktop?: AurumDesktopBridge;
    readonly chrome?: {
      readonly webview?: DesktopWebViewBridge;
    };
  }
}

const CAPABILITIES: readonly DesktopBridgeCapability[] = [
  "barcode-scanner",
  "cash-drawer",
  "file-export",
  "receipt-print",
];

export function getDesktopBridge(
  target: DesktopBridgeTarget = window,
): AurumDesktopBridge | null {
  return target.aurumDesktop ?? null;
}

export function hasDesktopBridge(target: DesktopBridgeTarget = window): boolean {
  return getDesktopBridge(target) !== null || target.chrome?.webview !== undefined;
}

export function getDesktopBridgeInfo(
  target: DesktopBridgeTarget = window,
): DesktopBridgeInfo {
  const bridge = getDesktopBridge(target);
  const isAvailable = hasDesktopBridge(target);

  return {
    appVersion: normalizeVersion(bridge?.appVersion),
    capabilities: normalizeCapabilities(bridge?.capabilities),
    isAvailable,
    platform: isAvailable ? (bridge?.platform ?? "windows") : null,
  };
}

export function hasDesktopCapability(
  capability: DesktopBridgeCapability,
  target: DesktopBridgeTarget = window,
): boolean {
  return getDesktopBridgeInfo(target).capabilities.includes(capability);
}

export function postDesktopMessage(
  message: DesktopBridgeMessage,
  target: DesktopBridgeTarget = window,
): boolean {
  const bridge = getDesktopBridge(target);
  if (typeof bridge?.postMessage === "function") {
    return tryPostMessage(bridge.postMessage, message);
  }

  const webview = target.chrome?.webview;
  if (typeof webview?.postMessage === "function") {
    return tryPostMessage(webview.postMessage, message);
  }

  return false;
}

function tryPostMessage(
  postMessage: (message: DesktopBridgeMessage) => void,
  message: DesktopBridgeMessage,
): boolean {
  try {
    postMessage(message);
    return true;
  } catch {
    return false;
  }
}

function normalizeVersion(value: string | undefined): string | null {
  const version = value?.trim();
  return version ? version : null;
}

function normalizeCapabilities(
  capabilities: readonly DesktopBridgeCapability[] | undefined,
): readonly DesktopBridgeCapability[] {
  if (!capabilities) {
    return [];
  }

  const unique = new Set<DesktopBridgeCapability>();
  for (const capability of capabilities) {
    if (CAPABILITIES.includes(capability)) {
      unique.add(capability);
    }
  }

  return [...unique].sort();
}
