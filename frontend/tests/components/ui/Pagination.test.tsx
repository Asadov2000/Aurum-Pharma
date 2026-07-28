import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "@/components/ui";

describe("Pagination", () => {
  it("shows the total and current page when total is known", () => {
    render(<Pagination page={2} pageSize={10} total={35} onPage={vi.fn()} />);
    expect(screen.getByText("Всего:")).toBeInTheDocument();
    expect(screen.getByText("35")).toBeInTheDocument();
    expect(screen.getByText("2 из 4")).toBeInTheDocument();
  });

  it("disables «Назад» on the first page", () => {
    const onPage = vi.fn();
    render(<Pagination page={1} pageSize={10} total={35} onPage={onPage} />);
    const prev = screen.getByRole("button", { name: /Назад/ });
    expect(prev).toBeDisabled();
    fireEvent.click(prev);
    expect(onPage).not.toHaveBeenCalled();
  });

  it("disables «Вперёд» on the last page", () => {
    render(<Pagination page={4} pageSize={10} total={35} onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Вперёд/ })).toBeDisabled();
  });

  it("calls onPage with the next/previous page", () => {
    const onPage = vi.fn();
    render(<Pagination page={2} pageSize={10} total={35} onPage={onPage} />);
    fireEvent.click(screen.getByRole("button", { name: /Вперёд/ }));
    expect(onPage).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByRole("button", { name: /Назад/ }));
    expect(onPage).toHaveBeenCalledWith(1);
  });

  it("supports the hasMore variant when there is no total", () => {
    const { rerender } = render(
      <Pagination page={1} pageSize={10} hasMore onPage={vi.fn()} />,
    );
    // No total → no "Всего", next enabled while hasMore.
    expect(screen.queryByText("Всего:")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Вперёд/ })).not.toBeDisabled();

    rerender(<Pagination page={3} pageSize={10} hasMore={false} onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Вперёд/ })).toBeDisabled();
  });
});
