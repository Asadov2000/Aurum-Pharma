import { isAxiosError } from "axios";

export function isConfirmedAuthFailure(error: unknown): boolean {
  if (!isAxiosError(error)) return false;
  return error.response?.status === 401 || error.response?.status === 403;
}

export function isTransientRefreshFailure(error: unknown): boolean {
  if (!isAxiosError(error)) return false;
  const status = error.response?.status;
  return status === undefined || status === 408 || status >= 500;
}
