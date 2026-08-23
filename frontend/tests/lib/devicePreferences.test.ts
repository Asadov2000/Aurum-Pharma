import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEVICE_PREFERENCES_CHANGED_EVENT,
  devicePreferencesKey,
  loadDevicePreferences,
  saveDevicePreferences,
} from "@/lib/devicePreferences";

describe("device preferences storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("migrates legacy POS keys without emitting a recursive sync event", () => {
    const changed = vi.fn();
    window.localStorage.setItem("pos:mode", "touch");
    window.localStorage.setItem("pos:beep", "1");
    window.localStorage.setItem("pos:lastRegisterId", "register-1");
    window.addEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, changed);

    const result = loadDevicePreferences("user-1:tenant-1");

    expect(result).toMatchObject({
      posMode: "touch",
      scannerSound: true,
      lastRegisterId: "register-1",
    });
    expect(window.localStorage.getItem(devicePreferencesKey("user-1:tenant-1"))).not.toBeNull();
    expect(changed).not.toHaveBeenCalled();
    window.removeEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, changed);

    expect(loadDevicePreferences("user-2:tenant-1")).toMatchObject({
      posMode: "auto",
      scannerSound: false,
      lastRegisterId: null,
    });
  });

  it("skips sync when storage is unavailable", () => {
    const changed = vi.fn();
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked");
    });
    window.addEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, changed);

    saveDevicePreferences("user-1:tenant-1", {
      posMode: "keyboard",
      scannerSound: true,
      receiptWidth: "80",
      lastRegisterId: "register-1",
    });

    expect(changed).not.toHaveBeenCalled();
    window.removeEventListener(DEVICE_PREFERENCES_CHANGED_EVENT, changed);
    setItem.mockRestore();
  });
});
