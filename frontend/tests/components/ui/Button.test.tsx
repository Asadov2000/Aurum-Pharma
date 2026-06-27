import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui";

describe("Button", () => {
  it("keeps its action label and disabled state while loading", () => {
    render(<Button isLoading>Сохранить</Button>);

    const button = screen.getByRole("button", { name: "Сохранить" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("does not set aria-busy when idle", () => {
    render(<Button>Сохранить</Button>);

    expect(screen.getByRole("button", { name: "Сохранить" })).not.toHaveAttribute(
      "aria-busy",
    );
  });
});
