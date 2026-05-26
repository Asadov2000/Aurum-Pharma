import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getWizard = vi.fn();
const getChecklist = vi.fn();
const submitWizardStep = vi.fn();
const startTrial = vi.fn();

vi.mock("@/features/onboarding/api", () => ({
  getWizard: (...a: unknown[]) => getWizard(...a),
  getChecklist: (...a: unknown[]) => getChecklist(...a),
  submitWizardStep: (...a: unknown[]) => submitWizardStep(...a),
  startTrial: (...a: unknown[]) => startTrial(...a),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

import { OnboardingPage } from "@/features/onboarding/OnboardingPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OnboardingPage />
    </QueryClientProvider>,
  );
}

const WIZARD = {
  tenant_id: "t-1",
  current_step: 3,
  steps_completed: [1, 2],
  wizard_data: {},
  is_completed: false,
  started_at: "2026-05-01T00:00:00Z",
  completed_at: null,
  updated_at: "2026-05-23T00:00:00Z",
};

const CHECKLIST_BELOW_100 = {
  tenant_id: "t-1",
  completed_tasks: ["first_sale"],
  catalog_items_count: 42,
  trial_eligible: false,
  trial_started_at: null,
  setup_ends_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z",
};

const CHECKLIST_ELIGIBLE = {
  ...CHECKLIST_BELOW_100,
  catalog_items_count: 120,
  trial_eligible: true,
};

describe("OnboardingPage", () => {
  beforeEach(() => {
    getWizard.mockReset();
    getChecklist.mockReset();
    submitWizardStep.mockReset();
    startTrial.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows current progress: 2 of 8 with check on completed steps", async () => {
    getWizard.mockResolvedValueOnce(WIZARD);
    getChecklist.mockResolvedValueOnce(CHECKLIST_BELOW_100);
    renderPage();
    expect(await screen.findByText(/2 из 8/i)).toBeInTheDocument();
    expect(screen.getByText("Профиль аптеки")).toBeInTheDocument();
    // Task chip rendered with the ru label.
    expect(screen.getByText(/Первая тестовая продажа/)).toBeInTheDocument();
  });

  it("hides Start-trial button when catalog has <100 items", async () => {
    getWizard.mockResolvedValueOnce(WIZARD);
    getChecklist.mockResolvedValueOnce(CHECKLIST_BELOW_100);
    renderPage();
    await screen.findByText(/2 из 8/i);
    expect(screen.queryByRole("button", { name: /Запустить пробный период/i })).toBeNull();
    expect(screen.getByText(/Каталог < 100/i)).toBeInTheDocument();
  });

  it("shows Start-trial button when eligible and POSTs on click", async () => {
    getWizard.mockResolvedValueOnce(WIZARD);
    getChecklist.mockResolvedValueOnce(CHECKLIST_ELIGIBLE);
    startTrial.mockResolvedValueOnce({
      tenant_id: "t-1",
      status: "trial",
      trial_started_at: "2026-05-23T00:00:00Z",
      trial_ends_at: "2026-06-06T00:00:00Z",
      subscription_id: "sub-1",
    });
    renderPage();
    const btn = await screen.findByRole("button", { name: /Запустить пробный период/i });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(startTrial).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText(/Пробный период активирован/i),
    ).toBeInTheDocument();
  });

  it("submits the step with a noted_at payload when 'Отметить' clicked", async () => {
    getWizard.mockResolvedValueOnce(WIZARD);
    getChecklist.mockResolvedValueOnce(CHECKLIST_BELOW_100);
    submitWizardStep.mockResolvedValueOnce({ ...WIZARD, steps_completed: [1, 2, 3] });
    renderPage();
    await screen.findByText(/2 из 8/i);
    // Step 3 ("Реквизиты для чека") is the current step; the first "Отметить"
    // button belongs to it.
    const buttons = screen.getAllByRole("button", { name: /Отметить/i });
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0]!);
    await waitFor(() => {
      expect(submitWizardStep).toHaveBeenCalledTimes(1);
    });
    const [step, payload] = submitWizardStep.mock.calls[0]!;
    expect(step).toBe(3);
    expect(payload).toHaveProperty("noted_at");
  });
});
