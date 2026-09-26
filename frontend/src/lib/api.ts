import axios, {
  CanceledError,
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import { getAuthSessionGeneration } from "@/stores/auth";
import { getSupportAccessSessionId } from "@/stores/supportAccess";

const DEV_API_BASE_URL = "http://localhost:8000/api/v1";
const SAME_ORIGIN_API_BASE_URL = "/api/v1";

interface ApiBaseUrlEnv {
  readonly DEV: boolean;
  readonly VITE_API_URL?: string;
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]);

function isLoopbackUrl(value: string): boolean {
  try {
    return LOOPBACK_HOSTS.has(new URL(value).hostname);
  } catch {
    return false;
  }
}

function isRemotePage(origin: string | undefined): boolean {
  if (!origin) return false;
  try {
    return !LOOPBACK_HOSTS.has(new URL(origin).hostname);
  } catch {
    return false;
  }
}

function getWindowOrigin(): string | undefined {
  return typeof window === "undefined" ? undefined : window.location.origin;
}

export function resolveApiBaseUrl(
  env: ApiBaseUrlEnv,
  pageOrigin: string | undefined = getWindowOrigin(),
): string {
  const configuredUrl = env.VITE_API_URL?.trim();
  if (configuredUrl) {
    if (isLoopbackUrl(configuredUrl) && isRemotePage(pageOrigin)) {
      return SAME_ORIGIN_API_BASE_URL;
    }
    return configuredUrl;
  }

  return env.DEV ? DEV_API_BASE_URL : SAME_ORIGIN_API_BASE_URL;
}

export const apiBaseUrl = resolveApiBaseUrl(import.meta.env);

export const api: AxiosInstance = axios.create({
  baseURL: apiBaseUrl,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

interface RetryConfig extends InternalAxiosRequestConfig {
  _authSessionGeneration?: number;
  _retry?: boolean;
  _skipAuth?: boolean;
  _skipRefresh?: boolean;
  _stepUpRetry?: boolean;
}

type TokenGetter = () => string | null;
type Refresher = () => Promise<string | null>;
type StepUpRequester = () => Promise<string | null>;
type OnAuthFailure = () => void;
type OnSupportAccessFailure = () => void;

let getAccess: TokenGetter = () => null;
let refresh: Refresher = async () => null;
let requestStepUp: StepUpRequester = async () => null;
let onAuthFailure: OnAuthFailure = () => {};
let onSupportAccessFailure: OnSupportAccessFailure = () => {};

export function configureAuthHooks(opts: {
  getAccessToken: TokenGetter;
  refreshTokens: Refresher;
  requestMfaStepUp: StepUpRequester;
  onAuthFailure: OnAuthFailure;
  onSupportAccessFailure?: OnSupportAccessFailure;
}): void {
  getAccess = opts.getAccessToken;
  refresh = opts.refreshTokens;
  requestStepUp = opts.requestMfaStepUp;
  onAuthFailure = opts.onAuthFailure;
  onSupportAccessFailure = opts.onSupportAccessFailure ?? (() => {});
}

function supportContextPath(config: InternalAxiosRequestConfig): string | null {
  const base = new URL(config.baseURL ?? apiBaseUrl, window.location.origin);
  const rawUrl = config.url ?? "";
  const isAbsolute = /^[a-z][a-z\d+.-]*:/i.test(rawUrl) || rawUrl.startsWith("//");
  const basePath = base.pathname.replace(/\/+$/, "");
  const target = isAbsolute
    ? new URL(rawUrl, window.location.origin)
    : rawUrl.startsWith(`${basePath}/`)
      ? new URL(rawUrl, base.origin)
      : new URL(rawUrl.replace(/^\/+/, ""), `${base.href.replace(/\/+$/, "")}/`);

  if (target.origin !== base.origin) return null;
  if (target.pathname !== basePath && !target.pathname.startsWith(`${basePath}/`)) return null;
  return target.pathname.slice(basePath.length) || "/";
}

function acceptsSupportContext(pathname: string | null, method: string | undefined): boolean {
  if (pathname === null) return false;
  const normalizedMethod = (method ?? "GET").toUpperCase();
  if (pathname === "/auth/me") return normalizedMethod === "GET";
  if (pathname === "/permissions" || pathname === "/templates") {
    return normalizedMethod === "GET";
  }
  if (pathname === "/roles" || pathname.startsWith("/roles/")) {
    return ["GET", "POST", "PATCH"].includes(normalizedMethod);
  }
  if (pathname === "/users" || pathname.startsWith("/users/")) {
    return ["GET", "POST", "PATCH", "DELETE"].includes(normalizedMethod);
  }
  if (pathname === "/branches" || pathname.startsWith("/branches/")) {
    return normalizedMethod === "GET";
  }
  return false;
}

api.interceptors.request.use((config) => {
  const cfg = config as RetryConfig;
  cfg._authSessionGeneration ??= getAuthSessionGeneration();
  if (cfg._authSessionGeneration !== getAuthSessionGeneration()) {
    throw new CanceledError("Authentication session changed", config);
  }
  config.headers.delete("X-Aurum-Support-Session");
  if (cfg._skipAuth) return config;
  const token = getAccess();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
    const supportAccessSessionId = getSupportAccessSessionId();
    const pathname = supportContextPath(config);
    if (supportAccessSessionId && acceptsSupportContext(pathname, config.method)) {
      config.headers.set("X-Aurum-Support-Session", supportAccessSessionId);
    }
  }
  return config;
});

// In-flight refresh promise — concurrent 401s share the same refresh call so
// we don't burn the refresh token twice and trigger reuse-detection.
interface SessionTokenRequest {
  generation: number;
  promise: Promise<string | null>;
}

let refreshing: SessionTokenRequest | null = null;

export function refreshAccessToken(
  generation = getAuthSessionGeneration(),
): Promise<string | null> {
  if (generation !== getAuthSessionGeneration()) return Promise.resolve(null);
  if (refreshing?.generation === generation) return refreshing.promise;
  const pending: SessionTokenRequest = {
    generation,
    promise: refresh()
      .then((token) => {
        if (generation !== getAuthSessionGeneration()) return null;
        if (!token) onAuthFailure();
        return token;
      })
      .finally(() => {
        if (refreshing === pending) refreshing = null;
      }),
  };
  refreshing = pending;
  return pending.promise;
}

let steppingUp: SessionTokenRequest | null = null;

export function requestStepUpAccessToken(
  generation = getAuthSessionGeneration(),
): Promise<string | null> {
  if (generation !== getAuthSessionGeneration()) return Promise.resolve(null);
  if (steppingUp?.generation === generation) return steppingUp.promise;
  const pending: SessionTokenRequest = {
    generation,
    promise: requestStepUp()
      .then((token) => (generation === getAuthSessionGeneration() ? token : null))
      .finally(() => {
        if (steppingUp === pending) steppingUp = null;
      }),
  };
  steppingUp = pending;
  return pending.promise;
}

export function isMfaStepUpRequired(error: AxiosError): boolean {
  if (error.response?.status !== 403) return false;
  const data = error.response.data as { error?: { details?: { reason?: unknown } } } | undefined;
  const reason = data?.error?.details?.reason;
  return (
    reason === "mfa_step_up_required" ||
    reason === "password_step_up_required" ||
    reason === "password_setup_required"
  );
}

export function isSupportAccessInactive(error: AxiosError): boolean {
  if (error.response?.status !== 403) return false;
  const data = error.response.data as { error?: { details?: { reason?: unknown } } } | undefined;
  return data?.error?.details?.reason === "support_access_inactive";
}

api.interceptors.response.use(
  (response) => {
    if ((response.config as RetryConfig)._authSessionGeneration !== getAuthSessionGeneration()) {
      throw new CanceledError("Authentication session changed", response.config);
    }
    return response;
  },
  async (error: AxiosError) => {
    const original = error.config as RetryConfig | undefined;
    if (!original || original._authSessionGeneration !== getAuthSessionGeneration()) {
      return Promise.reject(error);
    }
    const generation = original._authSessionGeneration;
    if (isSupportAccessInactive(error)) {
      onSupportAccessFailure();
      return Promise.reject(error);
    }
    if (original && !original._skipAuth && !original._stepUpRetry && isMfaStepUpRequired(error)) {
      original._stepUpRetry = true;
      const newAccess = await requestStepUpAccessToken(generation);
      if (!newAccess || generation !== getAuthSessionGeneration()) {
        return Promise.reject(error);
      }
      original.headers.set("Authorization", `Bearer ${newAccess}`);
      return api.request(original);
    }

    if (
      !original ||
      original._skipAuth ||
      original._skipRefresh ||
      original._retry ||
      error.response?.status !== 401
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    const newAccess = await refreshAccessToken(generation);
    if (!newAccess || generation !== getAuthSessionGeneration()) {
      return Promise.reject(error);
    }
    original.headers.set("Authorization", `Bearer ${newAccess}`);
    return api.request(original);
  },
);

export function withoutAuth(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...(config ?? {}), _skipAuth: true } as AxiosRequestConfig;
}

export function withoutRefresh(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...(config ?? {}), _skipRefresh: true } as AxiosRequestConfig;
}

// A proof request must not recursively open the dialog that is submitting it.
export function withoutStepUp(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...(config ?? {}), _skipRefresh: true, _stepUpRetry: true } as AxiosRequestConfig;
}
