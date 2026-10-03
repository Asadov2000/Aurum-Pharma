import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecordActionMenu } from "@/features/foundation/RecordActionMenu";

const navigate = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

describe("RecordActionMenu", () => {
  const writeText = vi.fn<(value: string) => Promise<void>>();
  beforeEach(() => {
    navigate.mockClear();
    writeText.mockReset().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  });
  const config = {
    enabled: true,
    order: ["open", "edit", "copy_name", "archive"],
    hidden: ["open"],
  };
  const props = {
    section: "catalog" as const,
    recordKey: "1",
    title: "Товар",
    label: "Действия товара",
    scope: "user:tenant",
    config,
    items: [{ id: "copy_name", label: "Скопировать название", copyValue: "Товар" }],
  };

  it("exposes only available commands and applies personal visibility", () => {
    render(
      <RecordActionMenu
        {...props}
        items={[...props.items, { id: "open", label: "Открыть", onSelect: vi.fn() }]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: props.label }));
    expect(screen.queryByRole("menuitem", { name: "Открыть" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Изменить" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("menuitem", { name: "Настроить меню" }));
    expect(navigate).toHaveBeenCalledWith({
      to: "/settings",
      search: { section: "quick-actions", actions: "catalog" },
    });
  });

  it("preserves custom ordering across visual groups and keeps dangerous actions last", () => {
    const onSelect = vi.fn();
    render(
      <RecordActionMenu
        {...props}
        config={{
          enabled: true,
          order: ["archive", "copy_name", "edit", "copy_barcode", "open"],
          hidden: [],
        }}
        items={[
          { id: "open", label: "Открыть", onSelect },
          { id: "edit", label: "Изменить", onSelect },
          { id: "copy_barcode", label: "Скопировать штрихкод", copyValue: "12345678" },
          ...props.items,
          { id: "archive", label: "В архив", tone: "danger", onSelect },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: props.label }));
    const labels = [
      "Скопировать название",
      "Изменить",
      "Скопировать штрихкод",
      "Открыть",
      "В архив",
      "Настроить меню",
    ];
    const commands = screen.getAllByRole("menuitem");
    expect(commands).toHaveLength(labels.length);
    labels.forEach((label, index) => expect(commands[index]).toHaveAccessibleName(label));
    expect(screen.getAllByText("Копирование")).toHaveLength(2);
    expect(screen.getAllByText("Действия")).toHaveLength(2);
    expect(writeText).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(commands[1]);
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("copies only on explicit command and shows safe failure feedback", async () => {
    writeText.mockRejectedValueOnce(new Error("Denied"));
    render(<RecordActionMenu {...props} />);
    fireEvent.click(screen.getByRole("button", { name: props.label }));
    expect(writeText).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("menuitem", { name: "Скопировать название" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Не удалось скопировать"),
    );
    expect(writeText).toHaveBeenCalledWith("Товар");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("dismisses the previous account menu and ignores its late clipboard result", async () => {
    let finishCopy!: () => void;
    writeText.mockReturnValue(
      new Promise<void>((resolve) => {
        finishCopy = resolve;
      }),
    );
    const { rerender } = render(<RecordActionMenu {...props} />);
    fireEvent.click(screen.getByRole("button", { name: props.label }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Скопировать название" }));
    fireEvent.click(screen.getByRole("button", { name: props.label }));
    rerender(<RecordActionMenu {...props} scope="another:tenant" />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    finishCopy();
    await waitFor(() => expect(writeText).toHaveResolved());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
