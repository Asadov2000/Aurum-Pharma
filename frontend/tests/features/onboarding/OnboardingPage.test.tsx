import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    "incoming.view",
    "users.view",
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
    search,
    children,
    className,
  }: {
    to: string;
    search?: Record<string, string>;
    children: React.ReactNode;
    className?: string;
  }) => {
    const query = search ? `?${new URLSearchParams(search).toString()}` : "";
    return (
      <a href={`${to}${query}`} className={className}>
        {children}
      </a>
    );
  },
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
    { code: "first_incoming", is_complete: false },
    { code: "first_sale", is_complete: true },
    { code: "second_user", is_complete: false },
    { code: "shift_opened", is_complete: false },
    { code: "test_receipt_printed", is_complete: false },
  ],
  required_completed: 3,
  required_total: 8,
  recommended_completed: 1,
  recommended_total: 5,
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
    onlineManager.setOnline(true);
    getOnboardingOverview.mockReset();
    startTrial.mockReset();
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000099");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    onlineManager.setOnline(true);
  });

  it("loads one canonical overview and shows the next available action", async () => {
    getOnboardingOverview.mockResolvedValueOnce(incompleteOverview);
    renderPage();

    expect(await screen.findByText("3 из 8")).toBeInTheDocument();
    expect(getOnboardingOverview).toHaveBeenCalledTimes(1);
    for (const link of screen.getAllByRole("link", { name: /Настроить точку/i })) {
      expect(link).toHaveAttribute("href", "/branches");
    }
    expect(screen.getByText("42 из 100")).toBeInTheDocument();
    expect(screen.getByText(/Осталось выполнить обязательных шагов: 4/)).toBeInTheDocument();
    expect(screen.getByText(/Плановая дата завершения настройки/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Начать пробный период/i })).toBeDisabled();
  });

  it("confirms the irreversible trial activation with one operation id", async () => {
    getOnboardingOverview
      .mockResolvedValueOnce(readyOverview)
      .mockResolvedValue({
        ...readyOverview,
        tenant_status: "trial",
        can_start_trial: false,
        trial_started_at: "2026-08-21T00:00:00Z",
        trial_ends_at: "2026-09-04T00:00:00Z",
      });
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
    expect(await screen.findByText("Пробный период активен")).toBeInTheDocument();
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

    for (const link of await screen.findAllByRole("link", { name: /Настроить оплату/i })) {
      expect(link).toHaveAttribute("href", "/settings?section=sales");
    }
  });

  it("offers direct actions for every unfinished owner recommendation", async () => {
    getOnboardingOverview.mockResolvedValueOnce(incompleteOverview);
    renderPage();

    expect(await screen.findByRole("link", { name: "Открыть приёмки" })).toHaveAttribute(
      "href",
      "/incoming",
    );
    expect(screen.getByRole("link", { name: "Добавить сотрудника" })).toHaveAttribute(
      "href",
      "/users",
    );
    expect(screen.getAllByText("Сначала настройте кассу и каталог")).toHaveLength(2);
  });

  it("allows the owner to test POS before starting the trial", async () => {
    const setupReadyForPos: OnboardingOverview = {
      ...incompleteOverview,
      steps: incompleteOverview.steps.map((step) =>
        step.code === "pos_settings"
          ? { ...step, is_complete: true, current: 1 }
          : step.code === "catalog"
            ? { ...step, current: 42 }
            : step,
      ),
      recommended_tasks: incompleteOverview.recommended_tasks.map((task) =>
        task.code === "first_sale" ? { ...task, is_complete: false } : task,
      ),
    };
    getOnboardingOverview.mockResolvedValueOnce(setupReadyForPos);
    renderPage();

    expect(await screen.findAllByRole("link", { name: "Проверить кассу" })).not.toHaveLength(0);
    expect(screen.getByRole("link", { name: "Провести продажу" })).toHaveAttribute("href", "/pos");
  });

  it("prioritizes unfinished required setup over POS for an active tenant", async () => {
    getOnboardingOverview.mockResolvedValueOnce({
      ...incompleteOverview,
      tenant_status: "active",
      subscription_id: "00000000-0000-4000-8000-000000000010",
    });
    renderPage();

    expect(await screen.findByText("Завершите обязательную настройку")).toBeInTheDocument();
    for (const link of screen.getAllByRole("link", { name: /Настроить точку/i })) {
      expect(link).toHaveAttribute("href", "/branches");
    }
    expect(screen.queryByRole("link", { name: /Открыть кассу/i })).toBeNull();
    expect(screen.getAllByText("Не выполнено:").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Выполнено:").length).toBeGreaterThan(0);
  });

  it("shows a failed activation inside the confirmation dialog", async () => {
    getOnboardingOverview.mockResolvedValue(readyOverview);
    startTrial.mockRejectedValue(new Error("network"));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Начать пробный период/i }));
    fireEvent.click(screen.getByRole("button", { name: "Начать период" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось начать пробный период");
    expect(screen.getByRole("button", { name: "Проверить статус" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить безопасно" }));
    await waitFor(() => expect(startTrial).toHaveBeenCalledTimes(2));
    expect(startTrial).toHaveBeenNthCalledWith(1, "00000000-0000-4000-8000-000000000099");
    expect(startTrial).toHaveBeenNthCalledWith(2, "00000000-0000-4000-8000-000000000099");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps cached readiness visible and disables activation while offline", async () => {
    let online = true;
    vi.spyOn(window.navigator, "onLine", "get").mockImplementation(() => online);
    getOnboardingOverview.mockResolvedValueOnce(readyOverview);
    renderPage();

    expect(await screen.findByText("Аптека готова к запуску")).toBeInTheDocument();
    online = false;
    act(() => window.dispatchEvent(new Event("offline")));
    expect(await screen.findByText("Нет сети.")).toBeInTheDocument();
    expect(screen.getByText(/загруженные ранее в этой сессии/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Начать пробный период/i })).toBeDisabled();
  });

  it("does not leave a cold offline start on an endless loading skeleton", async () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    getOnboardingOverview.mockReturnValue(new Promise(() => undefined));
    renderPage();

    expect(screen.getByRole("heading", { name: "Нет соединения с сервером" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ожидаем подключения" })).toBeDisabled();
    expect(screen.queryByLabelText("Проверяем готовность аптеки")).toBeNull();
  });

  it("fails closed when the readiness snapshot is incomplete", async () => {
    getOnboardingOverview.mockResolvedValueOnce({
      ...readyOverview,
      steps: readyOverview.steps.filter((step) => step.code !== "regulatory"),
    });
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Проверка готовности получена не полностью" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Начать пробный период/i })).toBeNull();
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
