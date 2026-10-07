import axios, {
  CanceledError,
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import { getAuthSessionGeneration } from "@/stores/auth";
import { getAuthEpoch } from "@/features/auth/logoutState";
import { getSupportAccessRevision, getSupportAccessSessionId } from "@/stores/supportAccess";

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
  _auth?: { generation: number; epoch: string | null };
  _supportRevision?: number;
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
  if (
    ["/auth/me", "/auth/profile", "/permissions", "/templates", "/branches"].includes(pathname) ||
    pathname.startsWith("/branches/")
  ) {
    return normalizedMethod === "GET";
  }
  if (pathname === "/roles" || pathname.startsWith("/roles/")) {
    return ["GET", "POST", "PATCH"].includes(normalizedMethod);
  }
  if (pathname === "/users" || pathname.startsWith("/users/")) {
    return ["GET", "POST", "PATCH", "DELETE"].includes(normalizedMethod);
  }
  return false;
}

function hasAuthSession(generation: number, epoch: string | null): boolean {
  return generation === getAuthSessionGeneration() && epoch === getAuthEpoch();
}

function hasCurrentAuthority(
  config: RetryConfig,
): config is RetryConfig & { _auth: { generation: number; epoch: string | null } } {
  return (
    config._auth !== undefined &&
    hasAuthSession(config._auth.generation, config._auth.epoch) &&
    (config._supportRevision === undefined ||
      config._supportRevision === getSupportAccessRevision())
  );
}

function assertCurrentAuthority(config: RetryConfig): void {
  if (!hasCurrentAuthority(config)) {
    throw new CanceledError("Authentication session changed", config);
  }
}

api.interceptors.request.use((config) => {
  const cfg = config as RetryConfig;
  cfg._auth ??= { generation: getAuthSessionGeneration(), epoch: getAuthEpoch() };
  const pathname = supportContextPath(config);
  if (
    acceptsSupportContext(pathname, config.method) ||
    (pathname === "/admin/support-access/sessions" && config.method?.toUpperCase() === "POST")
  ) {
    cfg._supportRevision ??= getSupportAccessRevision();
  }
  assertCurrentAuthority(cfg);
  config.headers.delete("X-Aurum-Support-Session");
  if (cfg._skipAuth) return config;
  const token = getAccess();
  assertCurrentAuthority(cfg);
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
    const supportAccessSessionId = getSupportAccessSessionId();
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
  epoch: string | null;
  promise: Promise<string | null>;
}

type SessionTokenKind = "refresh" | "stepUp";
const pendingTokenRequests: Record<SessionTokenKind, SessionTokenRequest | null> = {
  refresh: null,
  stepUp: null,
};

function requestSessionToken(
  kind: SessionTokenKind,
  generation = getAuthSessionGeneration(),
  epoch = getAuthEpoch(),
): Promise<string | null> {
  if (!hasAuthSession(generation, epoch)) {
    return Promise.resolve(null);
  }
  const current = pendingTokenRequests[kind];
  if (current?.generation === generation && current.epoch === epoch) return current.promise;
  const pending: SessionTokenRequest = {
    generation,
    epoch,
    promise: (kind === "refresh" ? refresh : requestStepUp)()
      .then((token) => {
        if (!hasAuthSession(generation, epoch)) return null;
        if (kind === "refresh" && !token) onAuthFailure();
        return token;
      })
      .finally(() => {
        if (pendingTokenRequests[kind] === pending) pendingTokenRequests[kind] = null;
      }),
  };
  pendingTokenRequests[kind] = pending;
  return pending.promise;
}

export function refreshAccessToken(
  generation?: number,
  epoch?: string | null,
): Promise<string | null> {
  return requestSessionToken("refresh", generation, epoch);
}

export function requestStepUpAccessToken(
  generation?: number,
  epoch?: string | null,
): Promise<string | null> {
  return requestSessionToken("stepUp", generation, epoch);
}

function accessFailureReason(error: AxiosError): unknown {
  if (error.response?.status !== 403) return undefined;
  const data = error.response.data as { error?: { details?: { reason?: unknown } } } | undefined;
  return data?.error?.details?.reason;
}

export function isMfaStepUpRequired(error: AxiosError): boolean {
  const reason = accessFailureReason(error);
  return (
    reason === "mfa_step_up_required" ||
    reason === "password_step_up_required" ||
    reason === "password_setup_required"
  );
}

export function isSupportAccessInactive(error: AxiosError): boolean {
  return accessFailureReason(error) === "support_access_inactive";
}

api.interceptors.response.use(
  (response) => {
    const cfg = response.config as RetryConfig;
    assertCurrentAuthority(cfg);
    return response;
  },
  async (error: AxiosError) => {
    const original = error.config as RetryConfig | undefined;
    if (!original || !hasCurrentAuthority(original)) {
      return Promise.reject(error);
    }
    const { generation, epoch } = original._auth;
    if (isSupportAccessInactive(error)) {
      if (original._supportRevision !== undefined) onSupportAccessFailure();
      return Promise.reject(error);
    }
    if (!original._skipAuth && !original._stepUpRetry && isMfaStepUpRequired(error)) {
      original._stepUpRetry = true;
      const newAccess = await requestStepUpAccessToken(generation, epoch);
      if (!newAccess || !hasCurrentAuthority(original)) {
        return Promise.reject(error);
      }
      original.headers.set("Authorization", `Bearer ${newAccess}`);
      return api.request(original);
    }

    if (
      original._skipAuth ||
      original._skipRefresh ||
      original._retry ||
      error.response?.status !== 401
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    const newAccess = await refreshAccessToken(generation, epoch);
    if (!newAccess || !hasCurrentAuthority(original)) {
      return Promise.reject(error);
    }
    original.headers.set("Authorization", `Bearer ${newAccess}`);
    return api.request(original);
  },
);

// Capture before Axios schedules its asynchronous request interceptors.
export function withAuthSession(
  config?: AxiosRequestConfig,
  bindSupportContext = false,
): AxiosRequestConfig {
  return {
    ...config,
    _auth: { generation: getAuthSessionGeneration(), epoch: getAuthEpoch() },
    ...(bindSupportContext ? { _supportRevision: getSupportAccessRevision() } : {}),
  } as AxiosRequestConfig;
}

export function withoutAuth(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...config, _skipAuth: true } as AxiosRequestConfig;
}

export function withoutRefresh(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...config, _skipRefresh: true } as AxiosRequestConfig;
}

// A proof request must not recursively open the dialog that is submitting it.
export function withoutStepUp(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...config, _skipRefresh: true, _stepUpRetry: true } as AxiosRequestConfig;
}
