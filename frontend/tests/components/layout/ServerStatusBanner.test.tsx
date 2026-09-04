import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ServerStatusBanner } from "@/components/layout/ServerStatusBanner";
import { ConnectivityProvider } from "@/lib/connectivity";

describe("ServerStatusBanner", () => {
  afterEach(() => {
    setOnlineStatus(true);
  });

  it("stays hidden when the server is healthy", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn(async () => true);

    renderBanner(checkHealth);

    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows a warning when the browser is online but the server is unavailable", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);

    expect(await screen.findByTestId("server-status-banner")).toHaveTextContent(
      "Сервер недоступен",
    );
  });

  it("treats health check errors as server unavailability", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn(async () => {
      throw new Error("health check failed");
    });

    renderBanner(checkHealth);

    expect(await screen.findByTestId("server-status-banner")).toBeInTheDocument();
  });

  it("does not check the server while the browser is offline", async () => {
    setOnlineStatus(false);
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);

    await waitFor(() => expect(checkHealth).not.toHaveBeenCalled());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("hides the server warning when the browser goes offline", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);
    expect(await screen.findByTestId("server-status-banner")).toBeInTheDocument();

    act(() => {
      setOnlineStatus(false);
      window.dispatchEvent(new Event("offline"));
    });

    expect(screen.queryByTestId("server-status-banner")).not.toBeInTheDocument();
  });
});

function renderBanner(checkHealth: () => Promise<boolean>): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ConnectivityProvider checkHealth={checkHealth} pollMs={60_000}>
        <ServerStatusBanner />
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
