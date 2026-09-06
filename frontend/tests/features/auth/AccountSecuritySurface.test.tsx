import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { cancelMfaStepUp, requestMfaStepUp } from "@/features/auth/stepUpCoordinator";

vi.mock("@/features/auth/MfaSetupPrompt", () => ({
  MfaSetupPrompt: () => <p>Необязательное предложение защиты</p>,
}));
vi.mock("@/features/auth/MfaStepUpDialog", () => ({
  MfaStepUpDialog: () => <p>Подтверждение паролем</p>,
}));

import AccountSecuritySurface from "@/features/auth/AccountSecuritySurface";

describe("account security loading boundary", () => {
  afterEach(() => act(() => cancelMfaStepUp()));

  it("shows the suggestion without loading a confirmation dialog", () => {
    render(<AccountSecuritySurface showPrompt />);
    expect(screen.getByText("Необязательное предложение защиты")).toBeInTheDocument();
    expect(screen.queryByText("Подтверждение паролем")).not.toBeInTheDocument();
  });

  it("still confirms actions on settings pages where the suggestion is hidden", async () => {
    const { container } = render(<AccountSecuritySurface showPrompt={false} />);
    expect(screen.queryByText("Необязательное предложение защиты")).not.toBeInTheDocument();
    act(() => {
      void requestMfaStepUp();
    });
    const confirmation = await screen.findByText("Подтверждение паролем");
    expect(confirmation).toBeInTheDocument();
    expect(container).not.toContainElement(confirmation);
  });
});
