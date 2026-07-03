import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { OfflineStatusBanner } from "@/components/layout/OfflineStatusBanner";

describe("OfflineStatusBanner", () => {
  afterEach(() => {
    setOnlineStatus(true);
  });

  it("stays hidden while the browser is online", () => {
    setOnlineStatus(true);

    render(<OfflineStatusBanner />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows an online-only warning when the browser is offline", () => {
    setOnlineStatus(false);

    render(<OfflineStatusBanner />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Нет связи. Касса работает только онлайн",
    );
  });

  it("reacts to online and offline browser events", () => {
    setOnlineStatus(true);
    render(<OfflineStatusBanner />);

    act(() => {
      setOnlineStatus(false);
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => {
      setOnlineStatus(true);
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
