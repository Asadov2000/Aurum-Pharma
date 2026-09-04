import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectivityProvider } from "@/lib/connectivity";
import { useConnectivity } from "@/lib/connectivityContext";

describe("ConnectivityProvider", () => {
  afterEach(() => setOnlineStatus(true));

  it("blocks server work after a failed health check and recovers active queries", async () => {
    setOnlineStatus(true);
    const checkHealth = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const refetch = vi.spyOn(queryClient, "refetchQueries").mockResolvedValue(undefined);

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth} pollMs={60_000}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("server-unavailable:false")).toBeInTheDocument();

    act(() => window.dispatchEvent(new Event("focus")));

    expect(await screen.findByText("online:true")).toBeInTheDocument();
    await waitFor(() => expect(refetch).toHaveBeenCalledWith({ type: "active" }));
  });

  it("does not call the API health endpoint while the browser is offline", async () => {
    setOnlineStatus(false);
    const checkHealth = vi.fn(async () => true);
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ConnectivityProvider checkHealth={checkHealth} pollMs={60_000}>
          <ConnectivityProbe />
        </ConnectivityProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText("offline:false")).toBeInTheDocument();
    await waitFor(() => expect(checkHealth).not.toHaveBeenCalled());
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
