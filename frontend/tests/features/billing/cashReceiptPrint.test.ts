import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { beginLogout, getAuthEpoch, markExplicitLogin } from "@/features/auth/logoutState";
import { clearClientSession } from "@/features/auth/session";
import { type MeResponse } from "@/features/auth/types";
import { printCashReceipt } from "@/features/billing/cashReceiptPrint";
import { type CashReceipt } from "@/features/billing/cashTypes";
import { useAuthStore } from "@/stores/auth";

const RECEIPT: CashReceipt = {
  id: "receipt-1",
  document_number: "AC-2026-0042",
  tenant_id: "tenant-1",
  tenant_name: "<img src=x onerror=alert(1)>",
  purpose: "<script>document.body.remove()</script>",
  amount: "600.00",
  currency: "TJS",
  paid_at: "2026-09-26T08:00:00Z",
  confirmed_at: "2026-09-26T08:15:00Z",
  allocated_amount: "250.00",
  credit_amount: "350.00",
  allocations: [
    { invoice_id: "invoice-1", invoice_number: "<svg onload=alert(2)>", amount: "250.00" },
  ],
  title: "Подтверждение приёма наличных",
};

function signIn(userId = "user-1", tenantId = "tenant-1"): void {
  const user: MeResponse = {
    id: userId,
    email: "",
    full_name: "",
    is_developer: false,
    is_administrator: false,
    home_tenant_id: tenantId,
    active_tenant_id: tenantId,
    status: "active",
    last_login_at: null,
    level: 10,
    is_tenant_owner: false,
    branch_assignments: {},
    permissions: [],
    platform_capabilities: [],
    support_access: null,
  };
  useAuthStore.setState({ accessToken: "synthetic-access", user, hydrated: true });
}

function deferredFonts() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function mockFramePrinting(fail = false, fonts?: Promise<void>) {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow");
  const nativeGetter: (() => Window | null) | undefined = descriptor?.get;
  const print = vi.fn(() => {
    if (fail) throw new Error("print failed");
  });
  const focus = vi.fn();
  const windows = new WeakSet<Window>();
  vi.spyOn(HTMLIFrameElement.prototype, "contentWindow", "get").mockImplementation(function (
    this: HTMLIFrameElement,
  ) {
    const printWindow = nativeGetter?.call(this) ?? null;
    if (printWindow && !windows.has(printWindow)) {
      windows.add(printWindow);
      vi.spyOn(printWindow, "focus").mockImplementation(focus);
      vi.spyOn(printWindow, "print").mockImplementation(print);
    }
    return printWindow;
  });
  if (fonts) {
    const docGetter = Object.getOwnPropertyDescriptor(
      HTMLIFrameElement.prototype,
      "contentDocument",
    )?.get;
    vi.spyOn(HTMLIFrameElement.prototype, "contentDocument", "get").mockImplementation(function (
      this: HTMLIFrameElement,
    ) {
      const doc: Document | null = docGetter?.call(this) ?? null;
      if (doc) Object.defineProperty(doc, "fonts", { configurable: true, value: { ready: fonts } });
      return doc;
    });
  }
  return { print, focus };
}

let scheduledFallbacks: number[] = [];
let clearedFallbacks: number[] = [];

function expectFallbackTimersCleared(): void {
  expect(scheduledFallbacks.length).toBeGreaterThan(0);
  for (const handle of scheduledFallbacks) {
    expect(clearedFallbacks.filter((cleared) => cleared === handle)).toHaveLength(1);
  }
}

describe("cash receipt printing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    clearClientSession();
    expect(markExplicitLogin(getAuthEpoch())).toBe(true);
    signIn();
    scheduledFallbacks = [];
    clearedFallbacks = [];
    const schedule = window.setTimeout.bind(window);
    const clear = window.clearTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation((handler, timeout, ...args) => {
      const handle = schedule(handler, timeout, ...args);
      if (timeout === 120_000) scheduledFallbacks.push(handle);
      return handle;
    });
    vi.spyOn(window, "clearTimeout").mockImplementation((handle) => {
      if (handle !== undefined) clearedFallbacks.push(handle);
      clear(handle);
    });
  });
  afterEach(() => {
    clearClientSession();
    document.querySelectorAll("iframe").forEach((frame) => frame.remove());
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("prints in an isolated frame with customer values as text and no executable markup", async () => {
    vi.useFakeTimers();
    const { print } = mockFramePrinting();
    await printCashReceipt(RECEIPT);

    const frame = document.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame).toHaveAttribute("sandbox", "allow-same-origin allow-modals");
    const printDocument = frame?.contentDocument;
    expect(printDocument?.body.textContent).toContain(RECEIPT.tenant_name);
    expect(printDocument?.body.textContent).toContain(RECEIPT.purpose);
    expect(printDocument?.body.textContent).toContain(RECEIPT.allocations[0]?.invoice_number);
    expect(printDocument?.querySelector("script,img,svg")).toBeNull();
    expect(printDocument?.querySelectorAll("[onerror],[onload]")).toHaveLength(0);
    expect(print).toHaveBeenCalledTimes(1);
    frame?.contentWindow?.dispatchEvent(new Event("afterprint"));
    expect(window.document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it("removes the print frame and rejects when the browser cannot print", async () => {
    vi.useFakeTimers();
    mockFramePrinting(true);
    await expect(printCashReceipt(RECEIPT)).rejects.toThrow("print failed");
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it("does not create a print document without an authenticated session", async () => {
    clearClientSession();
    const { print } = mockFramePrinting();
    await printCashReceipt(RECEIPT);
    expect(document.querySelector("iframe")).toBeNull();
    expect(print).not.toHaveBeenCalled();
    expect(scheduledFallbacks).toHaveLength(0);
  });

  it("preserves authorized support printing for a receipt from another tenant", async () => {
    const { print } = mockFramePrinting();
    const user = useAuthStore.getState().user;
    if (!user) throw new Error("Expected synthetic signed-in user");
    useAuthStore.getState().setUser({ ...user, is_administrator: true });
    await printCashReceipt({ ...RECEIPT, tenant_id: "tenant-2" });
    expect(print).toHaveBeenCalledOnce();
    document.querySelector("iframe")?.contentWindow?.dispatchEvent(new Event("afterprint"));
    expectFallbackTimersCleared();
  });

  it("does not print if focusing the document changes the session", async () => {
    const { print, focus } = mockFramePrinting();
    focus.mockImplementation(() => clearClientSession());
    await printCashReceipt(RECEIPT);
    expect(focus).toHaveBeenCalledOnce();
    expect(print).not.toHaveBeenCalled();
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it.each([
    ["logout", () => clearClientSession()],
    ["account switch", () => signIn("user-2")],
    ["tenant switch", () => signIn("user-1", "tenant-2")],
    [
      "new login by the same user",
      () => {
        clearClientSession();
        signIn();
      },
    ],
    ["logout intent before the auth store is cleared", () => beginLogout(null)],
  ])("cancels pending fonts on %s without waiting for fonts", async (_name, changeAuthority) => {
    const fonts = deferredFonts();
    const { print, focus } = mockFramePrinting(false, fonts.promise);
    const pending = printCashReceipt(RECEIPT);
    expect(document.querySelector("iframe")?.contentDocument?.fonts.ready).toBe(fonts.promise);
    expect(print).not.toHaveBeenCalled();
    changeAuthority();
    await expect(pending).resolves.toBeUndefined();
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
    fonts.resolve();
    await Promise.resolve();
    expect(focus).not.toHaveBeenCalled();
    expect(print).not.toHaveBeenCalled();
  });

  it.each([
    ["logout", () => clearClientSession()],
    ["account switch", () => signIn("user-2")],
    ["tenant switch", () => signIn("user-1", "tenant-2")],
  ])("removes an already printed document on %s", async (_name, changeAuthority) => {
    const { print } = mockFramePrinting();
    await printCashReceipt(RECEIPT);
    expect(print).toHaveBeenCalledOnce();
    expect(document.querySelector("iframe")).not.toBeNull();
    changeAuthority();
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it("times out while fonts remain pending and prevents a late print", async () => {
    const fonts = deferredFonts();
    const { print, focus } = mockFramePrinting(false, fonts.promise);
    const pending = printCashReceipt(RECEIPT);
    await vi.advanceTimersByTimeAsync(120_000);
    await expect(pending).resolves.toBeUndefined();
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
    fonts.resolve();
    await Promise.resolve();
    expect(focus).not.toHaveBeenCalled();
    expect(print).not.toHaveBeenCalled();
  });

  it("removes a printed document when afterprint never arrives", async () => {
    mockFramePrinting();
    await printCashReceipt(RECEIPT);
    await vi.advanceTimersByTimeAsync(119_999);
    expect(document.querySelector("iframe")).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it("rejects failed fonts and releases the document and fallback timer", async () => {
    const fonts = deferredFonts();
    const { print } = mockFramePrinting(false, fonts.promise);
    const pending = expect(printCashReceipt(RECEIPT)).rejects.toThrow("fonts failed");
    fonts.reject(new Error("fonts failed"));
    await pending;
    expect(print).not.toHaveBeenCalled();
    expect(document.querySelector("iframe")).toBeNull();
    expectFallbackTimersCleared();
  });

  it("keeps a pending job valid during token rotation and same-identity metadata changes", async () => {
    const fonts = deferredFonts();
    const { print } = mockFramePrinting(false, fonts.promise);
    const pending = printCashReceipt(RECEIPT);
    useAuthStore.setState({ accessToken: "synthetic-rotated-access" });
    const user = useAuthStore.getState().user;
    if (!user) throw new Error("Expected synthetic signed-in user");
    useAuthStore.getState().setUser({ ...user, full_name: "Synthetic updated name" });
    expect(document.querySelector("iframe")).not.toBeNull();
    fonts.resolve();
    await pending;
    expect(print).toHaveBeenCalledOnce();
    document.querySelector("iframe")?.contentWindow?.dispatchEvent(new Event("afterprint"));
    expectFallbackTimersCleared();
  });

  it("cleans every concurrent job and permits a new owner's subsequent print", async () => {
    const fonts = deferredFonts();
    const { print } = mockFramePrinting(false, fonts.promise);
    const first = printCashReceipt(RECEIPT);
    const second = printCashReceipt(RECEIPT);
    expect(document.querySelectorAll("iframe")).toHaveLength(2);
    signIn("user-2");
    await Promise.all([first, second]);
    expect(document.querySelectorAll("iframe")).toHaveLength(0);
    expectFallbackTimersCleared();
    fonts.resolve();
    await printCashReceipt(RECEIPT);
    expect(print).toHaveBeenCalledOnce();
    document.querySelector("iframe")?.contentWindow?.dispatchEvent(new Event("afterprint"));
    expect(document.querySelectorAll("iframe")).toHaveLength(0);
    expectFallbackTimersCleared();
  });

  it("unsubscribes session and afterprint handlers exactly once during cleanup", async () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    mockFramePrinting();
    await printCashReceipt(RECEIPT);
    const printWindow = document.querySelector("iframe")?.contentWindow;
    if (!printWindow) throw new Error("Expected synthetic print frame");
    const removePrint = vi.spyOn(printWindow, "removeEventListener");
    const sessionListeners = add.mock.calls.filter(
      ([type]) => type === "storage" || type === "focus" || type === "pageshow",
    );
    expect(sessionListeners).toHaveLength(3);
    printWindow.dispatchEvent(new Event("afterprint"));
    for (const [registeredType, registeredHandler] of sessionListeners) {
      expect(
        remove.mock.calls.filter(
          ([type, handler]) => type === registeredType && handler === registeredHandler,
        ),
      ).toHaveLength(1);
    }
    expect(removePrint.mock.calls.filter(([type]) => type === "afterprint")).toHaveLength(1);
    clearClientSession();
    printWindow.dispatchEvent(new Event("afterprint"));
    expect(removePrint.mock.calls.filter(([type]) => type === "afterprint")).toHaveLength(1);
    expectFallbackTimersCleared();
  });
});
