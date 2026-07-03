export const DESKTOP_BRIDGE_GLOBAL = "aurumDesktop";
export const DESKTOP_BARCODE_SCANNED_EVENT = "aurum-desktop-barcode-scanned";
export const DESKTOP_BARCODE_MAX_LENGTH = 256;

export type DesktopBridgeCapability =
  | "barcode-scanner"
  | "cash-drawer"
  | "file-export"
  | "receipt-print";

export type DesktopCashDrawerReason = "manual" | "sale-completed";

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
    }
  | {
      readonly type: "aurum.cash-drawer.open";
      readonly payload: DesktopCashDrawerOpenPayload;
    };

export interface DesktopBarcodeScanDetail {
  readonly code: string;
}

export type DesktopBarcodeScanEvent = CustomEvent<DesktopBarcodeScanDetail>;

export interface DesktopCashDrawerOpenPayload {
  readonly reason: DesktopCashDrawerReason;
  readonly registerId?: string;
  readonly saleId?: string;
}

export interface DesktopCashDrawerOpenRequest {
  readonly reason?: DesktopCashDrawerReason;
  readonly registerId?: string;
  readonly saleId?: string;
}

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
  interface WindowEventMap {
    "aurum-desktop-barcode-scanned": DesktopBarcodeScanEvent;
  }

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

export function notifyDesktopReady(target: DesktopBridgeTarget = window): boolean {
  return postDesktopMessage({ type: "aurum.desktop.ready" }, target);
}

export function requestDesktopReceiptPrint(
  saleId: string,
  target: DesktopBridgeTarget = window,
): boolean {
  const normalizedSaleId = saleId.trim();
  if (!normalizedSaleId || !supportsCapability("receipt-print", target)) {
    return false;
  }

  return postDesktopMessage(
    {
      payload: {
        saleId: normalizedSaleId,
      },
      type: "aurum.receipt.print",
    },
    target,
  );
}

export function requestDesktopCashDrawerOpen(
  request: DesktopCashDrawerOpenRequest = {},
  target: DesktopBridgeTarget = window,
): boolean {
  if (!supportsCapability("cash-drawer", target)) {
    return false;
  }

  const registerId = normalizeOptionalText(request.registerId);
  const saleId = normalizeOptionalText(request.saleId);
  const payload: DesktopCashDrawerOpenPayload = {
    reason: request.reason ?? "manual",
    ...(registerId ? { registerId } : {}),
    ...(saleId ? { saleId } : {}),
  };

  return postDesktopMessage(
    {
      payload,
      type: "aurum.cash-drawer.open",
    },
    target,
  );
}

export function dispatchDesktopBarcodeScan(
  rawCode: string,
  target: EventTarget = window,
): boolean {
  const code = normalizeDesktopBarcode(rawCode);
  if (!code) {
    return false;
  }

  return target.dispatchEvent(
    new CustomEvent(DESKTOP_BARCODE_SCANNED_EVENT, {
      detail: { code },
    }),
  );
}

export function normalizeDesktopBarcode(rawCode: string): string | null {
  const code = rawCode.trim();
  if (!code || code.length > DESKTOP_BARCODE_MAX_LENGTH) {
    return null;
  }

  return code;
}

function normalizeOptionalText(value: string | undefined): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
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

function supportsCapability(
  capability: DesktopBridgeCapability,
  target: DesktopBridgeTarget,
): boolean {
  const bridge = getDesktopBridge(target);
  if (bridge?.capabilities === undefined) {
    return hasDesktopBridge(target);
  }

  return hasDesktopCapability(capability, target);
}
