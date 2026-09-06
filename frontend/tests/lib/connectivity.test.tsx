import { act, render, screen, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectivityProvider } from "@/lib/connectivity";
import { useConnectivity } from "@/lib/connectivityContext";

describe("ConnectivityProvider", () => {
  afterEach(() => {
    setOnlineStatus(true);
    onlineManager.setOnline(true);
  });

  it("keeps server work available while one failed check is being confirmed", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("checking:true")).toBeInTheDocument();

    act(() => window.dispatchEvent(new Event("focus")));

    expect(await screen.findByText("online:true")).toBeInTheDocument();
    expect(onlineManager.isOnline()).toBe(true);
  });

  it("blocks after two consecutive failures and reconnects React Query after recovery", async () => {
    setOnlineStatus(true);
    const checkHealth = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("checking:true")).toBeInTheDocument();
    act(() => window.dispatchEvent(new Event("focus")));
    expect(await screen.findByText("server-unavailable:false")).toBeInTheDocument();
    expect(onlineManager.isOnline()).toBe(false);

    act(() => window.dispatchEvent(new Event("focus")));
    expect(await screen.findByText("online:true")).toBeInTheDocument();
    expect(onlineManager.isOnline()).toBe(true);
  });

  it("automatically retries a confirmed outage before the regular poll", async () => {
    vi.useFakeTimers();
    try {
      setOnlineStatus(true);
      const checkHealth = vi
        .fn()
        .mockResolvedValueOnce(false)
        .mockResolvedValueOnce(false)
        .mockResolvedValueOnce(true);
      const queryClient = new QueryClient();

      render(
        <QueryClientProvider client={queryClient}>
          <ConnectivityProvider checkHealth={checkHealth}>
            <ConnectivityProbe />
          </ConnectivityProvider>
        </QueryClientProvider>,
      );
      await act(async () => Promise.resolve());
      expect(screen.getByText("checking:true")).toBeInTheDocument();

      await act(async () => vi.advanceTimersByTimeAsync(1_500));
      expect(screen.getByText("server-unavailable:false")).toBeInTheDocument();

      await act(async () => vi.advanceTimersByTimeAsync(10_000));
      expect(screen.getByText("online:true")).toBeInTheDocument();
      expect(checkHealth).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not call the API health endpoint while the browser is offline", async () => {
    setOnlineStatus(false);
    const checkHealth = vi.fn(async () => true);
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText("offline:false")).toBeInTheDocument();
    await waitFor(() => expect(checkHealth).not.toHaveBeenCalled());
  });

  it("ignores a stale successful response after the browser goes offline", async () => {
    setOnlineStatus(true);
    let resolveHealth: ((healthy: boolean) => void) | undefined;
    const checkHealth = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveHealth = resolve;
        }),
    );
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1));

    act(() => {
      setOnlineStatus(false);
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByText("offline:false")).toBeInTheDocument();

    await act(async () => {
      resolveHealth?.(true);
      await Promise.resolve();
    });
    expect(screen.getByText("offline:false")).toBeInTheDocument();
  });

  it("runs a queued check after focus occurs during an in-flight failure", async () => {
    setOnlineStatus(true);
    let resolveFirst: ((healthy: boolean) => void) | undefined;
    const checkHealth = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<boolean>((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce(true);
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1));
    act(() => window.dispatchEvent(new Event("focus")));

    await act(async () => {
      resolveFirst?.(false);
      await Promise.resolve();
    });

    expect(await screen.findByText("online:true")).toBeInTheDocument();
    expect(checkHealth).toHaveBeenCalledTimes(2);
  });
});

function ConnectivityProbe(): JSX.Element {
  const connectivity = useConnectivity();
  return <div>{`${connectivity.status}:${connectivity.canUseServer}`}</div>;
}

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
