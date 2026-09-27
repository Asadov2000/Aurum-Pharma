import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type PersonalShiftList } from "@/features/my-work/types";
vi.mock("@/features/staff-pay/StaffPayPanel", () => ({ PersonalStaffPayPanel: () => null }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, ...props }: { to: string; children: React.ReactNode }) => <a href={to} {...props} />,
}));

const state = vi.hoisted(() => ({
  data: undefined as PersonalShiftList | undefined,
  dataUpdatedAt: Date.parse("2026-09-27T08:00:00Z"),
  isPending: false,
  isFetching: false,
  isError: false,
  error: new Error("unavailable"),
  refetch: vi.fn(),
  query: vi.fn(),
}));
vi.mock("@/features/my-work/queries", () => ({
  usePersonalShiftsQuery: (page: number) => {
    state.query(page);
    return state;
  },
}));

import { MyWorkPage } from "@/features/my-work/MyWorkPage";

const shifts: PersonalShiftList = {
  items: [
    {
      id: "own-shift",
      branch_id: "branch",
      branch_name: "Аптека на Рудаки",
      register_id: "register",
      register_name: "Касса 1",
      opened_at: "2026-09-27T06:00:00Z",
      closed_at: "2026-09-27T07:12:00Z",
      status: "closed",
      sales_total: "1234.50",
      returns_total: "25.00",
      currency: "TJS",
    },
  ],
  total: 1,
  page: 1,
  page_size: 10,
  timezone: "Asia/Dushanbe",
};

describe("my work", () => {
  beforeEach(() => {
    state.data = shifts;
    state.isPending = false;
    state.isFetching = false;
    state.isError = false;
    state.refetch.mockReset();
    state.query.mockReset();
  });

  it("shows own shift details in pharmacy timezone and elapsed time without payroll or management actions", () => {
    render(<MyWorkPage />);
    expect(screen.getByRole("heading", { name: "Моя работа" })).toBeInTheDocument();
    expect(screen.getByText("Аптека на Рудаки")).toBeInTheDocument();
    expect(screen.getByText("Касса 1")).toBeInTheDocument();
    expect(screen.getByText("Закрыта")).toBeInTheDocument();
    expect(screen.getByText("1 ч 12 мин")).toBeInTheDocument();
    const opened = new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "Asia/Dushanbe",
    }).format(new Date(shifts.items[0].opened_at));
    expect(screen.getByText(opened)).toBeInTheDocument();
    expect(screen.getByText(/1\s234,50 TJS/)).toBeInTheDocument();
    expect(screen.queryByText(/Недостача|Начислить/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Отчёты/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Мой профиль" })).toHaveAttribute("href", "/profile");
    expect(screen.getByText(/не табель рабочего времени/)).toBeInTheDocument();
  });

  it("shows an open shift without inventing closing time or worked hours", () => {
    state.data = { ...shifts, items: [{ ...shifts.items[0], status: "open", closed_at: null }] };
    render(<MyWorkPage />);
    expect(screen.getByText("Открыта")).toBeInTheDocument();
    expect(screen.getByText("Ещё не закрыта")).toBeInTheDocument();
    expect(screen.queryByText("Касса была открыта")).not.toBeInTheDocument();
  });

  it("distinguishes an empty own history from a loading or failed response", () => {
    state.data = { ...shifts, items: [], total: 0 };
    render(<MyWorkPage />);
    expect(screen.getByText(/У вас пока нет кассовых смен/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hides cached financial details after an error and offers retry", () => {
    state.isError = true;
    render(<MyWorkPage />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Аптека на Рудаки")).not.toBeInTheDocument();
    expect(screen.queryByText(/Данные на/)).not.toBeInTheDocument();
    expect(screen.getByText("Данные не обновлены")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(state.refetch).toHaveBeenCalledOnce();
  });

  it("labels suspended shift totals without presenting them as an open shift", () => {
    state.data = {
      ...shifts,
      items: [{ ...shifts.items[0], status: "suspended", closed_at: null }],
    };
    render(<MyWorkPage />);
    expect(screen.getByText("Приостановлена")).toBeInTheDocument();
    expect(screen.getByText("Итоги приостановленной смены")).toBeInTheDocument();
    expect(screen.queryByText("Промежуточные итоги открытой смены")).not.toBeInTheDocument();
  });

  it("does not show cached rows while another page is loading", () => {
    state.isPending = true;
    state.isFetching = true;
    render(<MyWorkPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка смен…");
    expect(screen.queryByRole("list", { name: "Ваши смены" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Обновление…" })).toBeDisabled();
  });

  it("refreshes explicitly and requests another page without retaining the previous rows", () => {
    state.data = { ...shifts, total: 11 };
    render(<MyWorkPage />);
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(state.refetch).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    expect(state.query).toHaveBeenLastCalledWith(2);
  });
});
