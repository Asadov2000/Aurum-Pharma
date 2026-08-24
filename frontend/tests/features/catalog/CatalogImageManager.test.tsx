import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const uploadCatalogImage = vi.fn();
const deleteCatalogImage = vi.fn();
const getCatalogItem = vi.fn();

vi.mock("@/features/catalog/mediaApi", () => ({
  uploadCatalogImage: (...args: unknown[]) => uploadCatalogImage(...args),
  deleteCatalogImage: (...args: unknown[]) => deleteCatalogImage(...args),
  getCatalogImage: vi.fn(),
}));

vi.mock("@/features/catalog/api", () => ({
  getCatalogItem: (...args: unknown[]) => getCatalogItem(...args),
}));

import { CatalogImageManager } from "@/features/catalog/CatalogImageManager";
import { type CatalogItem } from "@/features/catalog/types";

const ITEM: CatalogItem = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "t-1",
  brand_name: "Парацетамол",
  inn: null,
  manufacturer: null,
  form: null,
  dosage: null,
  pack_size: null,
  atx_code: null,
  dispensing_type: "otc",
  storage_type: "normal",
  category: null,
  base_price: null,
  currency: "TJS",
  image_version: null,
  is_active: true,
  deleted_at: null,
  created_at: "2026-08-23T00:00:00Z",
  updated_at: "2026-08-23T00:00:00Z",
};

function renderManager(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CatalogImageManager item={ITEM} canManage />
    </QueryClientProvider>,
  );
}

describe("CatalogImageManager", () => {
  beforeEach(() => {
    uploadCatalogImage.mockReset();
    deleteCatalogImage.mockReset();
    getCatalogItem.mockReset();
    getCatalogItem.mockResolvedValue({ ...ITEM, barcodes: [] });
  });

  it("uploads an optional PNG image", async () => {
    uploadCatalogImage.mockResolvedValueOnce({ ...ITEM, image_version: "version-1" });
    renderManager();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File([new Uint8Array([1, 2, 3])], "medicine.png", {
      type: "image/png",
    });
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(uploadCatalogImage).toHaveBeenCalledWith(ITEM.id, file);
    });
  });

  it("rejects unsupported files before calling the API", async () => {
    renderManager();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    const file = new File(["unsafe"], "medicine.svg", { type: "image/svg+xml" });
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("JPG или PNG");
    expect(uploadCatalogImage).not.toHaveBeenCalled();
  });
});
