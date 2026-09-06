import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectivityStatusBanner } from "@/components/layout/ConnectivityStatusBanner";
import { ConnectivityProvider } from "@/lib/connectivity";

describe("ConnectivityStatusBanner", () => {
  afterEach(() => {
    setOnlineStatus(true);
  });

  it("stays hidden while the browser and server are available", async () => {
    const checkHealth = vi.fn(async () => true);

    renderBanner(checkHealth);

    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows an online-only warning when the browser is offline", () => {
    setOnlineStatus(false);

    renderBanner(async () => true);

    expect(screen.getByRole("status")).toHaveTextContent("Нет интернета");
  });

  it("reacts to online and offline browser events", async () => {
    renderBanner(async () => true);
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

  it("shows a warning after two consecutive server failures", async () => {
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);
    await confirmSecondFailure(checkHealth);

    expect(await screen.findByRole("status")).toHaveTextContent("Сервер недоступен");
  });

  it("treats health check errors as server unavailability", async () => {
    const checkHealth = vi.fn(async () => {
      throw new Error("health check failed");
    });

    renderBanner(checkHealth);
    await confirmSecondFailure(checkHealth);

    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("does not check the server while the browser is offline", async () => {
    setOnlineStatus(false);
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);

    await waitFor(() => expect(checkHealth).not.toHaveBeenCalled());
    expect(screen.queryByText("Сервер недоступен", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Нет интернета");
  });

  it("replaces the server warning when the browser goes offline", async () => {
    const checkHealth = vi.fn(async () => false);

    renderBanner(checkHealth);
    await confirmSecondFailure(checkHealth);
    expect(await screen.findByRole("status")).toBeInTheDocument();

    act(() => {
      setOnlineStatus(false);
      window.dispatchEvent(new Event("offline"));
    });

    expect(screen.queryByText("Сервер недоступен", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Нет интернета");
  });
});

function renderBanner(checkHealth: () => Promise<boolean>): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ConnectivityProvider checkHealth={checkHealth}>
        <ConnectivityStatusBanner />
      </ConnectivityProvider>
    </QueryClientProvider>,
  );
}

async function confirmSecondFailure(checkHealth: ReturnType<typeof vi.fn>): Promise<void> {
  await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1));
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await Promise.resolve();
  });
}

function setOnlineStatus(isOnline: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: isOnline,
  });
}
