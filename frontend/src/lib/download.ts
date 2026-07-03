import { requestDesktopFileExport } from "@/lib/desktopBridge";

export function downloadBlob(blob: Blob, filename: string): void {
  requestDesktopFileExport({
    fileName: filename,
    mimeType: blob.type,
    sizeBytes: blob.size,
  });

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
