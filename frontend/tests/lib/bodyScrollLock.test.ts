import { describe, expect, it } from "vitest";

import { acquireBodyScrollLock } from "@/lib/bodyScrollLock";

describe("body scroll lock", () => {
  it("restores individual overflow axes and priorities without changing other styles", () => {
    const originalStyle = document.body.style.cssText;
    document.body.style.setProperty("overflow-y", "scroll", "important");
    document.body.style.setProperty("padding-right", "7px");
    const release = acquireBodyScrollLock();
    try {
      expect(document.body.style.overflow).toBe("hidden");
      release();
      expect(document.body.style.getPropertyValue("overflow-x")).toBe("");
      expect(document.body.style.getPropertyValue("overflow-y")).toBe("scroll");
      expect(document.body.style.getPropertyPriority("overflow-y")).toBe("important");
      expect(document.body.style.paddingRight).toBe("7px");
    } finally {
      release();
      document.body.style.cssText = originalStyle;
    }
  });

  it("does not release another overlay's lock when cleanup runs twice", () => {
    const originalOverflow = document.body.style.overflow;
    const releaseFirst = acquireBodyScrollLock();
    const releaseSecond = acquireBodyScrollLock();
    try {
      releaseFirst();
      releaseFirst();
      expect(document.body.style.overflow).toBe("hidden");
      releaseSecond();
      expect(document.body.style.overflow).toBe(originalOverflow);
    } finally {
      releaseFirst();
      releaseSecond();
    }
  });
});
