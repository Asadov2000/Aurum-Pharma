import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  defaultQuickActionPreferences,
  type QuickActionSection,
} from "@/features/settings/quickActions";

const state = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("@/features/settings/useQuickActionPreferences", () => ({
  useQuickActionPreferences: (section: QuickActionSection) => ({
    config: defaultQuickActionPreferences(section),
    scope: "user-1:tenant-1",
    save: state.save,
    hasPending: false,
    error: null,
  }),
}));

import { QuickActionsSettingsPanel } from "@/features/settings/QuickActionsSettingsPanel";

describe("QuickActionsSettingsPanel", () => {
  it("opens the originating section and labels document commands correctly", () => {
    render(<QuickActionsSettingsPanel initialSection="incoming" />);
    expect(screen.getByRole("button", { name: "Приёмка товаров" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("checkbox", { name: "Показывать в меню: Скопировать номер документа" }),
    ).toBeChecked();
    expect(screen.queryByText("Изменить товар")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сотрудники" }));
    expect(
      screen.queryByRole("button", { name: "Выше: Уволить / отозвать приглашение" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Показывать в меню: Настроить доступ" }),
    ).toBeChecked();
  });
  beforeEach(() => {
    state.save.mockReset();
  });

  it("previews drafts and cancels without saving or applying them", () => {
    render(<QuickActionsSettingsPanel />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Показывать в меню: Изменить товар" }));
    const preview = screen.getByRole("complementary", { name: "Предпросмотр быстрых действий" });
    expect(within(preview).queryByText("Изменить товар")).not.toBeInTheDocument();
    expect(state.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(within(preview).getByText("Изменить товар")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить настройки" })).toBeDisabled();
    expect(state.save).not.toHaveBeenCalled();
  });

  it("keeps danger pinned, resets only the draft, and saves explicitly", async () => {
    render(<QuickActionsSettingsPanel />);
    expect(screen.queryByRole("button", { name: "Выше: Архивировать" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Выше: Изменить товар" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Меню по правой кнопке" }));
    expect(state.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Вернуть стандартные настройки" }));
    expect(screen.getByRole("checkbox", { name: "Меню по правой кнопке" })).toBeChecked();
    expect(state.save).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Показывать в меню: Скопировать название" }),
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));
    });
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.save.mock.calls[0]?.[0]).toMatchObject({ enabled: true, hidden: ["copy_name"] });
    expect(state.save.mock.calls[0]?.[0].order.at(-1)).toBe("archive");
  });

  it("switches independent sections without applying an abandoned draft", () => {
    render(<QuickActionsSettingsPanel />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Меню по правой кнопке" }));
    fireEvent.click(screen.getByRole("button", { name: "Партии" }));
    expect(screen.getByRole("checkbox", { name: "Меню по правой кнопке" })).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Показывать в меню: Скопировать номер партии" }),
    ).toBeChecked();
    expect(
      screen.queryByRole("checkbox", { name: "Показывать в меню: Архивировать" }),
    ).not.toBeInTheDocument();
    expect(state.save).not.toHaveBeenCalled();
  });

  it("explains why an all-hidden draft cannot be saved", () => {
    render(<QuickActionsSettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Партии" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Показывать в меню: Открыть карточку" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Показывать в меню: Скопировать номер партии" }),
    );
    expect(
      screen.getByText("Оставьте хотя бы одну команду видимой, чтобы сохранить настройки."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить настройки" })).toBeDisabled();
    expect(state.save).not.toHaveBeenCalled();
  });
});
