import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { type Notification, type Subscription } from "@/features/notifications/types";

function renderPageWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <NotificationsPage />
    </QueryClientProvider>,
  );
  return qc;
}

const renderPage = () => renderPageWithClient();

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
    listSubscriptions.mockResolvedValue([]);
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
    expect(screen.getAllByText("Истекает лицензия").length).toBeGreaterThanOrEqual(1);
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

    await waitFor(() => {
      expect(screen.getAllByText("Вход с нового устройства").length).toBeGreaterThanOrEqual(1);
    });
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

  it("shows the inbox and settings together without a tab switch", async () => {
    listNotifications.mockResolvedValueOnce([]);
    listSubscriptions.mockResolvedValueOnce([]);
    renderPage();
    await screen.findByText(/Пока нет уведомлений/i);
    expect(screen.getByRole("heading", { name: "Входящие" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Настройки уведомлений" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подписки" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Инбокс" })).not.toBeInTheDocument();
    expect(await screen.findByText("Истекает лицензия")).toBeInTheDocument();
    expect(screen.getByText(/Заканчивается пробный период/i)).toBeInTheDocument();
    expect(screen.getByText("Обязательно")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox", { name: "В системе" })[0]).toBeDisabled();
    // Phase-two channels are not rendered as misleading controls before they exist.
    expect(screen.queryByRole("checkbox", { name: "Telegram" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "SMS" })).not.toBeInTheDocument();
  });

  it("shows a single-read failure and keeps the notification actionable", async () => {
    listNotifications.mockResolvedValue([UNREAD]);
    markRead.mockRejectedValueOnce(new Error("network"));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Прочитано" }));

    expect(await screen.findByText(/Не удалось отметить/i)).toBeInTheDocument();
    expect(markRead).toHaveBeenCalledWith(UNREAD.id);
    expect(screen.getByText(UNREAD.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Прочитано" })).toBeEnabled();
    expect(listNotifications).toHaveBeenCalledTimes(1);
  });

  it("marks all notifications and refreshes the unread count", async () => {
    listNotifications.mockResolvedValueOnce([UNREAD]).mockResolvedValue([READ]);
    markAllRead.mockResolvedValueOnce({ marked: 1 });
    renderPage();
    await screen.findByText(UNREAD.title);

    fireEvent.click(screen.getByRole("button", { name: "Отметить все" }));

    await waitFor(() => expect(markAllRead).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(listNotifications).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(READ.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отметить все" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Прочитано" })).not.toBeInTheDocument();
  });

  it("passes unread and severity filters to the API while keeping settings visible", async () => {
    listNotifications.mockResolvedValue([]);
    renderPage();
    await screen.findByText(/Пока нет уведомлений/i);

    fireEvent.click(screen.getByRole("button", { name: "Непрочитанные", exact: true }));
    fireEvent.change(screen.getByRole("combobox", { name: "Уровень" }), {
      target: { value: "critical" },
    });

    await waitFor(() => {
      expect(listNotifications).toHaveBeenLastCalledWith({
        unread_only: true,
        severity: "critical",
        page: 1,
        page_size: 100,
      });
    });
    expect(screen.getByRole("heading", { name: "Настройки уведомлений" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить настройки" })).toBeInTheDocument();
  });

  it("hides cached notifications after a list error and retries the request", async () => {
    listNotifications
      .mockResolvedValueOnce([UNREAD])
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce([READ]);
    const qc = renderPageWithClient();
    await screen.findByText(UNREAD.title);

    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["notifications", "list"] });
    });

    expect(await screen.findByText(/Не удалось загрузить уведомления/i)).toBeInTheDocument();
    expect(screen.queryByText(UNREAD.title)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Прочитано" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отметить все" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Повторить/i }));

    expect(await screen.findByText(READ.title)).toBeInTheDocument();
    expect(listNotifications).toHaveBeenCalledTimes(3);
    expect(screen.queryByText(/Не удалось загрузить уведомления/i)).not.toBeInTheDocument();
  });

  it("loads the next page without showing stale notifications during the request", async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      ...UNREAD,
      id: `n-page-1-${index}`,
      title: `Уведомление первой страницы ${index}`,
    }));
    let resolvePage: ((items: Notification[]) => void) | undefined;
    const nextPage = new Promise<Notification[]>((resolve) => {
      resolvePage = resolve;
    });
    listNotifications.mockResolvedValueOnce(firstPage).mockReturnValueOnce(nextPage);
    renderPage();
    await screen.findByText(firstPage[0]!.title);

    fireEvent.click(screen.getByRole("button", { name: "Далее" }));

    await waitFor(() => {
      expect(listNotifications).toHaveBeenLastCalledWith({
        unread_only: false,
        severity: undefined,
        page: 2,
        page_size: 100,
      });
    });
    expect(screen.queryByText(firstPage[0]!.title)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отметить все" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();

    await act(async () => resolvePage?.([READ]));

    expect(await screen.findByText(READ.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
  });

  it("keeps settings edits while marking read and refreshing inbox and subscriptions", async () => {
    listNotifications.mockResolvedValueOnce([UNREAD]).mockResolvedValue([READ]);
    listSubscriptions
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        { event_type: "trial_ending", channels: ["in_app", "email"], is_enabled: true },
      ] satisfies Subscription[]);
    markRead.mockResolvedValueOnce(undefined);
    const qc = renderPageWithClient();
    const receiving = await screen.findByRole("checkbox", {
      name: "Получать сообщения: Истекает лицензия",
    });
    fireEvent.click(receiving);
    expect(receiving).not.toBeChecked();

    fireEvent.click(await screen.findByRole("button", { name: "Прочитано" }));
    await screen.findByText(READ.title);
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["notifications", "subscriptions"] });
    });

    expect(
      screen.getByRole("checkbox", {
        name: "Получать сообщения: Истекает лицензия",
      }),
    ).not.toBeChecked();
    expect(markRead).toHaveBeenCalledWith(UNREAD.id);
    expect(listSubscriptions).toHaveBeenCalledTimes(2);
  });

  it("saves edited settings through the API and preserves mandatory channels", async () => {
    listNotifications.mockResolvedValue([]);
    listSubscriptions.mockResolvedValue([
      {
        event_type: "security.new_device_login",
        channels: ["email"],
        is_enabled: false,
      },
    ] satisfies Subscription[]);
    patchSubscriptions.mockImplementation(async (items: Subscription[]) => items);
    renderPage();
    expect(await screen.findByText("Обязательно")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox", { name: "В системе" })[0]).toBeChecked();
    expect(screen.getAllByRole("checkbox", { name: "В системе" })[0]).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Получать сообщения: Истекает лицензия",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));

    await waitFor(() => {
      expect(patchSubscriptions).toHaveBeenCalledWith(
        expect.arrayContaining([
          { event_type: "license_expiring", channels: ["in_app"], is_enabled: false },
          {
            event_type: "security.new_device_login",
            channels: expect.arrayContaining(["in_app", "email"]),
            is_enabled: true,
          },
        ]),
      );
    });
    expect(await screen.findByText("Настройки уведомлений сохранены.")).toBeInTheDocument();
  });

  it("keeps settings edits after a save failure and retries the same payload", async () => {
    listNotifications.mockResolvedValue([]);
    patchSubscriptions
      .mockRejectedValueOnce(new Error("network"))
      .mockImplementationOnce(async (items: Subscription[]) => items);
    renderPage();
    const receiving = await screen.findByRole("checkbox", {
      name: "Получать сообщения: Истекает лицензия",
    });
    fireEvent.click(receiving);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));

    expect(await screen.findByText(/Не удалось сохранить/i)).toBeInTheDocument();
    expect(receiving).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Повторить сохранение" }));

    expect(await screen.findByText("Настройки уведомлений сохранены.")).toBeInTheDocument();
    expect(patchSubscriptions).toHaveBeenCalledTimes(2);
    expect(patchSubscriptions.mock.calls[1]).toEqual(patchSubscriptions.mock.calls[0]);
  });

  it("blocks saving an enabled event without an available channel", async () => {
    listNotifications.mockResolvedValue([]);
    listSubscriptions.mockResolvedValue([
      { event_type: "license_expiring", channels: ["telegram"], is_enabled: true },
    ] satisfies Subscription[]);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Для включённых сообщений выберите хотя бы один способ получения: В системе или Email.",
    );
    const save = screen.getByRole("button", { name: "Сохранить настройки" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(patchSubscriptions).not.toHaveBeenCalled();

    const event = screen.getByRole("region", { name: "Истекает лицензия" });
    fireEvent.click(within(event).getByRole("checkbox", { name: "В системе" }));
    expect(save).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves an unknown subscription and its channels when saving known edits", async () => {
    const unknown: Subscription = {
      event_type: "future.inventory_event",
      channels: ["email", "telegram", "sms"],
      is_enabled: false,
    };
    listNotifications.mockResolvedValue([]);
    listSubscriptions.mockResolvedValue([unknown]);
    patchSubscriptions.mockImplementation(async (items: Subscription[]) => items);
    renderPage();
    fireEvent.click(
      await screen.findByRole("checkbox", {
        name: "Получать сообщения: Истекает лицензия",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));

    await waitFor(() => {
      expect(patchSubscriptions).toHaveBeenCalledWith(
        expect.arrayContaining([
          unknown,
          { event_type: "license_expiring", channels: ["in_app"], is_enabled: false },
        ]),
      );
    });
    expect(await screen.findByText("Настройки уведомлений сохранены.")).toBeInTheDocument();
  });
});
