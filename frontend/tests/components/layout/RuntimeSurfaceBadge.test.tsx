import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RuntimeSurfaceBadge } from "@/components/layout/RuntimeSurfaceBadge";

describe("RuntimeSurfaceBadge", () => {
  it("shows the browser surface compactly", () => {
    render(<RuntimeSurfaceBadge surface="browser" />);

    const badge = screen.getByTestId("runtime-surface-badge");
    expect(badge).toHaveTextContent("Веб");
    expect(badge).toHaveAccessibleName("Режим запуска: Открыто в браузере");
    expect(badge).toHaveAttribute("title", "Открыто в браузере");
  });

  it("shows the installed PWA surface", () => {
    render(<RuntimeSurfaceBadge surface="pwa" />);

    const badge = screen.getByTestId("runtime-surface-badge");
    expect(badge).toHaveTextContent("PWA");
    expect(badge).toHaveAccessibleName(
      "Режим запуска: Установлено как приложение",
    );
  });

  it("shows the Windows desktop surface", () => {
    render(<RuntimeSurfaceBadge surface="windows-desktop" />);

    const badge = screen.getByTestId("runtime-surface-badge");
    expect(badge).toHaveTextContent("Windows");
    expect(badge).toHaveAccessibleName(
      "Режим запуска: Открыто в Windows-приложении",
    );
  });
});
