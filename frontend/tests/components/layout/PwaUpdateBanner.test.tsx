import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PwaUpdateBanner } from "@/components/layout/PwaUpdateBanner";
import { PWA_UPDATE_READY_EVENT } from "@/lib/pwa";

describe("PwaUpdateBanner", () => {
  it("stays hidden until the PWA update event is emitted", () => {
    render(<PwaUpdateBanner />);

    expect(screen.queryByTestId("pwa-update-banner")).not.toBeInTheDocument();
  });

  it("shows an update prompt after the PWA update event", () => {
    render(<PwaUpdateBanner />);

    fireEvent(window, new Event(PWA_UPDATE_READY_EVENT));

    expect(screen.getByTestId("pwa-update-banner")).toHaveTextContent(
      "Доступно обновление приложения",
    );
  });

  it("reloads the app when the user accepts the update", () => {
    const reload = vi.fn();
    render(<PwaUpdateBanner reload={reload} />);

    fireEvent(window, new Event(PWA_UPDATE_READY_EVENT));
    fireEvent.click(screen.getByTestId("pwa-update-reload-button"));

    expect(reload).toHaveBeenCalledTimes(1);
  });
});
