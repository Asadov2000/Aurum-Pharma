import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import NumPad from "@/features/pos/NumPad";

describe("NumPad", () => {
  it("builds a value from taps and submits it", () => {
    const onSubmit = vi.fn();
    render(<NumPad title="Количество" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "1" }));
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).toHaveBeenCalledWith("12");
  });

  it("replaces a prefilled value with the first digit", () => {
    const onSubmit = vi.fn();
    render(<NumPad title="t" initial="5" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).toHaveBeenCalledWith("7");
  });

  it("backspace removes the last entered character", () => {
    const onSubmit = vi.fn();
    render(<NumPad title="t" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "5" }));
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    fireEvent.click(screen.getByRole("button", { name: "⌫" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).toHaveBeenCalledWith("5");
  });

  it("disables the decimal point when allowDecimal is false", () => {
    render(<NumPad title="t" allowDecimal={false} onSubmit={vi.fn()} onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "." })).toBeDisabled();
  });

  it("does not submit an empty value", () => {
    const onSubmit = vi.fn();
    render(<NumPad title="t" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not submit zero or accept more than two decimal places", () => {
    const onSubmit = vi.fn();
    const first = render(<NumPad title="t" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "0" }));
    fireEvent.click(screen.getByRole("button", { name: "." }));
    fireEvent.click(screen.getByRole("button", { name: "1" }));
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "3" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).toHaveBeenCalledWith("0.12");

    onSubmit.mockClear();
    first.unmount();
    render(<NumPad title="t" onSubmit={onSubmit} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "0" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
