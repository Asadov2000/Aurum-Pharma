import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { OfflineStatusBanner } from "@/components/layout/OfflineStatusBanner";
import { ConnectivityProvider } from "@/lib/connectivity";

describe("OfflineStatusBanner", () => {
  afterEach(() => {
    setOnlineStatus(true);
  });

  it("stays hidden while the browser is online", async () => {
    setOnlineStatus(true);

    renderBanner();
    await act(async () => Promise.resolve());

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows an online-only warning when the browser is offline", () => {
    setOnlineStatus(false);

    renderBanner();

    expect(screen.getByRole("status")).toHaveTextContent(
      "Нет связи. Касса работает только онлайн",
    );
  });

  it("reacts to online and offline browser events", async () => {
    setOnlineStatus(true);
    renderBanner();
    await act(async () => Promise.resolve());

    act(() => {
      setOnlineStatus(false);
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(async () => {
      setOnlineStatus(true);
      window.dispatchEvent(new Event("online"));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });
});

function renderBanner(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ConnectivityProvider checkHealth={async () => true} pollMs={60_000}>
        <OfflineStatusBanner />
      </ConnectivityProvider>
    </QueryClientProvider>,
  );
}

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
