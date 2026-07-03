import { downloadBlob } from "@/lib/download";
import { beforeEach, describe, expect, it, vi } from "vitest";

const desktopBridge = vi.hoisted(() => ({
  requestDesktopFileExport: vi.fn(),
}));

vi.mock("@/lib/desktopBridge", () => ({
  requestDesktopFileExport: desktopBridge.requestDesktopFileExport,
}));

describe("downloadBlob", () => {
  const createObjectURL = vi.fn(() => "blob:aurum-download");
  const revokeObjectURL = vi.fn();
  const click = vi.fn();

  beforeEach(() => {
    desktopBridge.requestDesktopFileExport.mockReset();
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    click.mockClear();
    document.body.innerHTML = "";

    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
  });

  it("notifies the desktop host before falling back to browser download", () => {
    const blob = new Blob(["report"], { type: "application/pdf" });

    downloadBlob(blob, "report.pdf");

    expect(desktopBridge.requestDesktopFileExport).toHaveBeenCalledWith({
      fileName: "report.pdf",
      mimeType: "application/pdf",
      sizeBytes: blob.size,
    });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:aurum-download");
    expect(document.querySelector('a[download="report.pdf"]')).toBeNull();
  });
});
