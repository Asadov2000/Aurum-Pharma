import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const stepUpMfa = vi.fn();

vi.mock("@/features/auth/api", () => ({
  stepUpMfa: (...args: unknown[]) => stepUpMfa(...args),
}));

import { MfaStepUpDialog } from "@/features/auth/MfaStepUpDialog";
import { cancelMfaStepUp, requestMfaStepUp } from "@/features/auth/stepUpCoordinator";
import { useAuthStore } from "@/stores/auth";

describe("MfaStepUpDialog", () => {
  beforeEach(() => {
    cancelMfaStepUp();
    stepUpMfa.mockReset();
    useAuthStore.getState().clear();
  });

  it("validates the code without calling the API", async () => {
    render(<MfaStepUpDialog />);
    act(() => {
      void requestMfaStepUp();
    });

    fireEvent.change(await screen.findByLabelText("Код подтверждения"), {
      target: { value: "123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(await screen.findByText("Код состоит из 6 цифр")).toBeInTheDocument();
    expect(stepUpMfa).not.toHaveBeenCalled();
  });

  it("stores the renewed token and resolves the blocked request", async () => {
    stepUpMfa.mockResolvedValueOnce({
      access_token: "renewed-access",
      token_type: "bearer",
      expires_in: 900,
    });
    render(<MfaStepUpDialog />);
    let pending!: Promise<string | null>;
    act(() => {
      pending = requestMfaStepUp();
    });

    fireEvent.change(await screen.findByLabelText("Код подтверждения"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() => {
      expect(stepUpMfa).toHaveBeenCalledWith("123456");
      expect(useAuthStore.getState().accessToken).toBe("renewed-access");
    });
    await expect(pending).resolves.toBe("renewed-access");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels the blocked request without logging the user out", async () => {
    useAuthStore.getState().setTokens({
      access_token: "current-access",
      token_type: "bearer",
      expires_in: 900,
    });
    render(<MfaStepUpDialog />);
    let pending!: Promise<string | null>;
    act(() => {
      pending = requestMfaStepUp();
    });

    fireEvent.click(await screen.findByRole("button", { name: "Отмена" }));

    await expect(pending).resolves.toBeNull();
    expect(useAuthStore.getState().accessToken).toBe("current-access");
  });
});
