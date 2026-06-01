/**
 * Trigger a browser download for an in-memory Blob (e.g. an authed PDF/XLSX
 * fetched via axios). The browser/OS opens it in the right app; we can't
 * auto-open Excel from the web, so a download is the closest we get.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
