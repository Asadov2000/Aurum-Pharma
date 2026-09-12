import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const startEnrollment = vi.fn();
const verify = vi.fn();
const authenticated = vi.fn();
vi.mock("@/features/auth/api", () => ({
  startMfaEnrollment: (...args: unknown[]) => startEnrollment(...args),
  verifyMfa: (...args: unknown[]) => verify(...args),
  completeMfaEnrollment: vi.fn(),
  recoverMfa: vi.fn(),
}));

import { MfaLoginFlow } from "@/features/auth/MfaLoginFlow";
import { type TokenPair } from "@/features/auth/types";

describe("deferred MFA flow lifecycle", () => {
  beforeEach(() => {
    startEnrollment.mockReset();
    verify.mockReset();
    authenticated.mockReset().mockResolvedValue(undefined);
  });

  it("starts enrollment only once during StrictMode effect replay", async () => {
    startEnrollment.mockResolvedValue({
      secret: "example-enrollment-secret",
      recovery_codes: ["AAAAA-BBBBB-CCCCC-22222"],
      provisioning_uri: "otpauth://totp/test",
      expires_in: 300,
    });
    render(
      <StrictMode>
        <MfaLoginFlow
          challenge={{
            status: "mfa_enrollment_required",
            challenge_token: "enrollment-challenge",
            expires_in: 300,
          }}
          onAuthenticated={authenticated}
          onBack={vi.fn()}
          onEnrollment={vi.fn()}
        />
      </StrictMode>,
    );
    expect(await screen.findByText("example-enrollment-secret")).toBeInTheDocument();
    expect(startEnrollment).toHaveBeenCalledTimes(1);
    expect(authenticated).not.toHaveBeenCalled();
  });

  it("aborts verification and ignores a late response after leaving the flow", async () => {
    let resolveVerification: (tokens: TokenPair) => void = () => undefined;
    verify.mockImplementation(
      () =>
        new Promise<TokenPair>((resolve) => {
          resolveVerification = resolve;
        }),
    );
    const rendered = render(
      <MfaLoginFlow
        challenge={{ status: "mfa_required", challenge_token: "verify-challenge", expires_in: 300 }}
        onAuthenticated={authenticated}
        onBack={vi.fn()}
        onEnrollment={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Код подтверждения"), { target: { value: "123456" } });
    fireEvent.submit(
      screen.getByRole("button", { name: "Подтвердить", exact: true }).closest("form")!,
    );
    await waitFor(() => expect(verify).toHaveBeenCalledTimes(1));
    const signal = verify.mock.calls[0]?.[1] as AbortSignal;
    expect(signal.aborted).toBe(false);
    rendered.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      resolveVerification({ access_token: "late-token", token_type: "bearer", expires_in: 900 });
    });
    expect(authenticated).not.toHaveBeenCalled();
  });
});
