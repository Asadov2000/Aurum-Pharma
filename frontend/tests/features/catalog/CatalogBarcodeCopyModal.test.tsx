import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCatalogItem = vi.fn();
vi.mock("@/features/catalog/api", () => ({
  getCatalogItem: (...args: unknown[]) => getCatalogItem(...args),
}));

import { CatalogBarcodeCopyModal } from "@/features/catalog/CatalogBarcodeCopyModal";

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <CatalogBarcodeCopyModal itemId="chosen-item" onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

describe("CatalogBarcodeCopyModal", () => {
  beforeEach(() => {
    getCatalogItem.mockReset();
  });

  it("loads the chosen product and copies only the barcode selected by the user", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    getCatalogItem.mockResolvedValue({
      barcodes: [
        { id: "code-1", code: "0012345678905", code_type: "ean13" },
        { id: "code-2", code: "A-20", code_type: "code128" },
      ],
    });
    renderModal();
    const code = await screen.findByText("A-20");
    expect(writeText).not.toHaveBeenCalled();
    expect(getCatalogItem).toHaveBeenCalledExactlyOnceWith("chosen-item", expect.any(AbortSignal));
    fireEvent.click(within(code.closest("li")!).getByRole("button", { name: "Скопировать" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith("A-20"));
    expect(await screen.findByRole("status")).toHaveTextContent("Штрихкод скопирован.");
  });

  it("shows a loading state without a copy action", () => {
    getCatalogItem.mockReturnValue(new Promise(() => undefined));
    renderModal();
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка штрихкодов");
    expect(screen.queryByRole("button", { name: "Скопировать" })).not.toBeInTheDocument();
  });

  it("explains when the product has no barcodes", async () => {
    getCatalogItem.mockResolvedValue({ barcodes: [] });
    renderModal();
    expect(await screen.findByText("У этого товара пока нет штрихкодов.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Скопировать" })).not.toBeInTheDocument();
  });

  it("retries a failed barcode request", async () => {
    getCatalogItem
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ barcodes: [] });
    renderModal();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("У этого товара пока нет штрихкодов.")).toBeInTheDocument();
    expect(getCatalogItem).toHaveBeenCalledTimes(2);
  });

  it("keeps the code visible with manual-copy guidance if the clipboard is unavailable", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    getCatalogItem.mockResolvedValue({
      barcodes: [{ id: "code-1", code: "0012345678905", code_type: "ean13" }],
    });
    renderModal();
    fireEvent.click(await screen.findByRole("button", { name: "Скопировать" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Выделите код и скопируйте его вручную",
    );
    expect(screen.getByText("0012345678905")).toBeInTheDocument();
  });
});
