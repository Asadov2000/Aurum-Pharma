import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PwaInstallButton } from "@/components/layout/PwaInstallButton";
import { type BeforeInstallPromptEvent } from "@/lib/pwa";

describe("PwaInstallButton", () => {
  it("stays hidden until the browser allows installation", () => {
    render(<PwaInstallButton surface="browser" />);

    expect(
      screen.queryByRole("button", { name: "Установить приложение" }),
    ).not.toBeInTheDocument();
  });

  it("captures beforeinstallprompt and opens the browser install prompt", async () => {
    const prompt = vi.fn().mockResolvedValue(undefined);
    const event = createBeforeInstallPromptEvent(prompt, "accepted");
    render(<PwaInstallButton surface="browser" />);

    act(() => {
      window.dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(true);
    const button = await screen.findByRole("button", {
      name: "Установить приложение",
    });

    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(prompt).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Установить приложение" }),
      ).not.toBeInTheDocument();
    });
  });

  it("hides when the appinstalled event fires", async () => {
    const prompt = vi.fn().mockResolvedValue(undefined);
    render(<PwaInstallButton surface="browser" />);

    act(() => {
      window.dispatchEvent(createBeforeInstallPromptEvent(prompt, "dismissed"));
    });
    expect(
      await screen.findByRole("button", { name: "Установить приложение" }),
    ).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Установить приложение" }),
      ).not.toBeInTheDocument();
    });
  });

  it("does not register the install prompt in already installed surfaces", () => {
    const prompt = vi.fn().mockResolvedValue(undefined);
    render(<PwaInstallButton surface="pwa" />);

    act(() => {
      window.dispatchEvent(createBeforeInstallPromptEvent(prompt, "accepted"));
    });

    expect(
      screen.queryByRole("button", { name: "Установить приложение" }),
    ).not.toBeInTheDocument();
    expect(prompt).not.toHaveBeenCalled();
  });
});

function createBeforeInstallPromptEvent(
  prompt: () => Promise<void>,
  outcome: "accepted" | "dismissed",
): BeforeInstallPromptEvent {
  const event = new Event("beforeinstallprompt", {
    cancelable: true,
  }) as BeforeInstallPromptEvent;

  Object.defineProperties(event, {
    platforms: { value: ["web"] },
    prompt: { value: prompt },
    userChoice: {
      value: Promise.resolve({
        outcome,
        platform: "web",
      }),
    },
  });

  return event;
}
