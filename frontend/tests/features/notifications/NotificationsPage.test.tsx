import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listNotifications = vi.fn();
const markRead = vi.fn();
const markAllRead = vi.fn();
const listSubscriptions = vi.fn();
const patchSubscriptions = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...props }: ComponentProps<"a"> & { to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/features/notifications/api", () => ({
  listNotifications: (...a: unknown[]) => listNotifications(...a),
  markRead: (...a: unknown[]) => markRead(...a),
  markAllRead: (...a: unknown[]) => markAllRead(...a),
  listSubscriptions: (...a: unknown[]) => listSubscriptions(...a),
  patchSubscriptions: (...a: unknown[]) => patchSubscriptions(...a),
}));

import { NotificationsPage } from "@/features/notifications/NotificationsPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NotificationsPage />
    </QueryClientProvider>,
  );
}

const UNREAD = {
  id: "n-1",
  tenant_id: "t-1",
  user_id: "u-1",
  event_type: "license_expiring",
  title: "Лицензия скоро истекает",
  body: "У точки «Demo» срок действия лицензии — 2026-06-15.",
  data: null,
  severity: "warning" as const,
  read_at: null,
  created_at: "2026-05-23T08:00:00Z",
};

const READ = {
  ...UNREAD,
  id: "n-2",
  title: "Импорт завершён",
  event_type: "import_completed",
  severity: "info" as const,
  read_at: "2026-05-22T09:00:00Z",
};

const SECURITY_ALERT = {
  ...UNREAD,
  id: "n-security",
  event_type: "security.new_device_login",
  title: "Вход с нового устройства",
  body: "Если это были не вы, проверьте активные сеансы.",
  data: { reason: "new_device", action: "review_sessions" },
};

describe("NotificationsPage", () => {
  beforeEach(() => {
    listNotifications.mockReset();
    markRead.mockReset();
    markAllRead.mockReset();
    listSubscriptions.mockReset();
    patchSubscriptions.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the inbox with severity badge and event title", async () => {
    listNotifications.mockResolvedValueOnce([UNREAD]);
    renderPage();
    expect(await screen.findByText("Лицензия скоро истекает")).toBeInTheDocument();
    // Severity badge ru label — also appears in the filter <option>, so accept
    // both occurrences (badge in the row + dropdown option).
    expect(screen.getAllByText("Предупреждение").length).toBeGreaterThanOrEqual(1);
    // Known-event title used as a sub-label
    expect(screen.getByText("Истекает лицензия")).toBeInTheDocument();
    // Unread counter
    expect(screen.getByText(/непрочитанных:/)).toBeInTheDocument();
  });

  it("marks a single notification as read", async () => {
    listNotifications.mockResolvedValue([UNREAD]);
    markRead.mockResolvedValueOnce(undefined);
    renderPage();
    const btn = await screen.findByRole("button", { name: /Прочитано/i });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(markRead).toHaveBeenCalledWith("n-1");
    });
  });

  it("links a new-device warning to session security", async () => {
    listNotifications.mockResolvedValueOnce([SECURITY_ALERT]);
    renderPage();

    expect(await screen.findByText("Вход с нового устройства")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Проверить сеансы" })).toHaveAttribute(
      "href",
      "/security",
    );
  });

  it("disables 'Отметить все' when nothing is unread", async () => {
    listNotifications.mockResolvedValueOnce([READ]);
    renderPage();
    const btn = await screen.findByRole("button", { name: /Отметить все/i });
    expect(btn).toBeDisabled();
  });

  it("shows an inline error when marking all notifications fails", async () => {
    listNotifications.mockResolvedValue([UNREAD]);
    markAllRead.mockRejectedValueOnce(new Error("network"));
    renderPage();
    await screen.findByText("Лицензия скоро истекает");
    const btn = screen.getByRole("button", { name: /Отметить все/i });

    fireEvent.click(btn);

    await waitFor(() => {
      expect(markAllRead).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText(/Не удалось отметить/i)).toBeInTheDocument();
  });

  it("switches to Subscriptions tab and shows the known event catalog", async () => {
    listNotifications.mockResolvedValueOnce([]);
    listSubscriptions.mockResolvedValueOnce([]);
    renderPage();
    await screen.findByText(/Пока нет уведомлений/i);
    fireEvent.click(screen.getByRole("button", { name: "Подписки" }));
    expect(await screen.findByText("Истекает лицензия")).toBeInTheDocument();
    expect(screen.getByText(/Заканчивается пробный период/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Обязательное" })).toBeDisabled();
    expect(screen.getAllByRole("checkbox", { name: "В системе" })[0]).toBeDisabled();
    // Phase-two channels are not rendered as misleading controls before they exist.
    expect(screen.queryByText(/Telegram/i)).not.toBeInTheDocument();
  });
});
