import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type OnboardingOverview } from "@/features/onboarding/types";

const getOnboardingOverview = vi.fn();
const startTrial = vi.fn();

vi.mock("@/features/onboarding/api", () => ({
  getOnboardingOverview: (...args: unknown[]) => getOnboardingOverview(...args),
  startTrial: (...args: unknown[]) => startTrial(...args),
}));

const user = {
  active_tenant_id: "00000000-0000-4000-8000-000000000001",
  home_tenant_id: "00000000-0000-4000-8000-000000000001",
  is_developer: false,
  is_administrator: false,
  is_tenant_owner: true,
  permissions: [
    "settings.update",
    "branches.view",
    "registers.view",
    "catalog.view",
    "pos.sell",
    "billing.overview.view",
    "billing.invoice.view",
  ],
  platform_capabilities: [],
};

vi.mock("@/features/auth/queries", () => ({
  useMeQuery: () => ({ data: user }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    children,
    className,
  }: {
    to: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

import { OnboardingPage } from "@/features/onboarding/OnboardingPage";

const incompleteOverview: OnboardingOverview = {
  tenant_id: user.active_tenant_id,
  tenant_name: "Аптека Сино",
  tenant_status: "setup",
  setup_ends_at: "2026-09-01T00:00:00Z",
  trial_started_at: null,
  trial_ends_at: null,
  subscription_id: null,
  steps: [
    { code: "pharmacy_profile", is_complete: true, required: true, current: null, target: null },
    { code: "licensed_branch", is_complete: false, required: true, current: 0, target: 1 },
    { code: "receipt_details", is_complete: false, required: true, current: 0, target: 1 },
    { code: "tenant_owner", is_complete: true, required: true, current: 1, target: 1 },
    { code: "catalog", is_complete: false, required: true, current: 42, target: 100 },
    { code: "pos_settings", is_complete: false, required: true, current: 0, target: 1 },
    { code: "regulatory", is_complete: true, required: true, current: null, target: null },
    { code: "ready", is_complete: false, required: true, current: null, target: null },
  ],
  recommended_tasks: [
    { code: "catalog_loaded", is_complete: false },
    { code: "first_incoming", is_complete: false },
    { code: "first_sale", is_complete: true },
    { code: "second_user", is_complete: false },
    { code: "shift_opened", is_complete: false },
    { code: "test_receipt_printed", is_complete: false },
  ],
  required_completed: 3,
  required_total: 8,
  recommended_completed: 1,
  recommended_total: 6,
  is_ready: false,
  can_start_trial: false,
  blocker_codes: ["licensed_branch", "receipt_details", "catalog", "pos_settings"],
};

const readyOverview: OnboardingOverview = {
  ...incompleteOverview,
  steps: incompleteOverview.steps.map((step) => ({ ...step, is_complete: true })),
  required_completed: 8,
  is_ready: true,
  can_start_trial: true,
  blocker_codes: [],
};

function renderPage(): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OnboardingPage />
    </QueryClientProvider>,
  );
}

describe("OnboardingPage", () => {
  beforeEach(() => {
    getOnboardingOverview.mockReset();
    startTrial.mockReset();
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000099");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads one canonical overview and shows the next available action", async () => {
    getOnboardingOverview.mockResolvedValueOnce(incompleteOverview);
    renderPage();

    expect(await screen.findByText("3 из 8")).toBeInTheDocument();
    expect(getOnboardingOverview).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: /Настроить точку/i })).toHaveAttribute(
      "href",
      "/branches",
    );
    expect(screen.getByText("42 из 100")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Начать пробный период/i })).toBeNull();
  });

  it("confirms the irreversible trial activation with one operation id", async () => {
    getOnboardingOverview.mockResolvedValue(readyOverview);
    startTrial.mockResolvedValueOnce({
      tenant_id: readyOverview.tenant_id,
      status: "trial",
      trial_started_at: "2026-08-21T00:00:00Z",
      trial_ends_at: "2026-09-04T00:00:00Z",
      subscription_id: "00000000-0000-4000-8000-000000000010",
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Начать пробный период/i }));
    expect(screen.getByText(/повторно активировать/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Начать период" }));

    await waitFor(() => {
      expect(startTrial).toHaveBeenCalledWith("00000000-0000-4000-8000-000000000099");
    });
  });

  it("routes an existing register with missing payments to pharmacy settings", async () => {
    const paymentBlocked: OnboardingOverview = {
      ...readyOverview,
      steps: readyOverview.steps.map((step) =>
        step.code === "pos_settings"
          ? {
              ...step,
              is_complete: false,
              current: 0,
              action_hint: "payment_methods_missing" as const,
            }
          : step,
      ),
      required_completed: 7,
      is_ready: false,
      can_start_trial: false,
      blocker_codes: ["pos_settings"],
    };
    getOnboardingOverview.mockResolvedValueOnce(paymentBlocked);
    renderPage();

    expect(await screen.findByRole("link", { name: /Настроить оплату/i })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("prioritizes unfinished required setup over POS for an active tenant", async () => {
    getOnboardingOverview.mockResolvedValueOnce({
      ...incompleteOverview,
      tenant_status: "active",
      subscription_id: "00000000-0000-4000-8000-000000000010",
    });
    renderPage();

    expect(await screen.findByText("Завершите обязательную настройку")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Настроить точку/i })).toHaveAttribute(
      "href",
      "/branches",
    );
    expect(screen.queryByRole("link", { name: /Открыть кассу/i })).toBeNull();
    expect(screen.getAllByText("Не выполнено:").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Выполнено:").length).toBeGreaterThan(0);
  });

  it("shows a failed activation inside the confirmation dialog", async () => {
    getOnboardingOverview.mockResolvedValue(readyOverview);
    startTrial.mockRejectedValueOnce(new Error("network"));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Начать пробный период/i }));
    fireEvent.click(screen.getByRole("button", { name: "Начать период" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось начать пробный период",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps cached readiness visible and disables activation while offline", async () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    getOnboardingOverview.mockResolvedValueOnce(readyOverview);
    renderPage();

    expect(await screen.findByText("Нет сети.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Начать пробный период/i })).toBeDisabled();
  });

  it("shows a controlled retry state when readiness cannot be loaded", async () => {
    getOnboardingOverview.mockRejectedValueOnce(new Error("network"));
    getOnboardingOverview.mockResolvedValueOnce(incompleteOverview);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Проверить снова/i }));
    expect(await screen.findByText("3 из 8")).toBeInTheDocument();
    expect(getOnboardingOverview).toHaveBeenCalledTimes(2);
  });
});
