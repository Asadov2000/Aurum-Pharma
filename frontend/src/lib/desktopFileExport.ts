import {
  postDesktopMessage,
  supportsCapability,
  type DesktopBridgeTarget,
  type DesktopFileExportPayload,
  type DesktopFileExportRequest,
} from "./desktopRuntime";

export const DESKTOP_FILE_NAME_MAX_LENGTH = 180;

export function requestDesktopFileExport(
  request: DesktopFileExportRequest,
  target: DesktopBridgeTarget = window,
): boolean {
  if (!supportsCapability("file-export", target)) {
    return false;
  }

  const fileName = normalizeDesktopFileName(request.fileName);
  if (!fileName) {
    return false;
  }

  const mimeType = normalizeOptionalMimeType(request.mimeType);
  const sizeBytes = normalizeOptionalSizeBytes(request.sizeBytes);
  const payload: DesktopFileExportPayload = {
    fileName,
    ...(mimeType ? { mimeType } : {}),
    ...(sizeBytes !== null ? { sizeBytes } : {}),
  };

  return postDesktopMessage(
    {
      payload,
      type: "aurum.file-export.request",
    },
    target,
  );
}

export function normalizeDesktopFileName(rawFileName: string): string | null {
  const fileName = replaceUnsafeFileNameChars(rawFileName.trim())
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "");

  if (!fileName || fileName.length > DESKTOP_FILE_NAME_MAX_LENGTH) {
    return null;
  }

  if (isReservedWindowsFileName(fileName)) {
    return null;
  }

  return fileName;
}

function normalizeOptionalText(value: string | undefined): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

function normalizeOptionalMimeType(value: string | undefined): string | null {
  const mimeType = normalizeOptionalText(value);
  if (
    !mimeType ||
    mimeType.length > 128 ||
    !/^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/i.test(mimeType)
  ) {
    return null;
  }

  return mimeType;
}

function normalizeOptionalSizeBytes(value: number | undefined): number | null {
  if (value === undefined) {
    return null;
  }

  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function replaceUnsafeFileNameChars(value: string): string {
  let result = "";
  for (const char of value) {
    const code = char.charCodeAt(0);
    result += code <= 31 || '<>:"/\\|?*'.includes(char) ? "_" : char;
  }

  return result;
}

function isReservedWindowsFileName(fileName: string): boolean {
  const stem = fileName.split(".")[0]?.toUpperCase();
  return (
    stem === "CON" ||
    stem === "PRN" ||
    stem === "AUX" ||
    stem === "NUL" ||
    /^COM[1-9]$/.test(stem ?? "") ||
    /^LPT[1-9]$/.test(stem ?? "")
  );
}
