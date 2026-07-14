import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

const DEV_API_BASE_URL = "http://localhost:8000/api/v1";
const SAME_ORIGIN_API_BASE_URL = "/api/v1";

interface ApiBaseUrlEnv {
  readonly DEV: boolean;
  readonly VITE_API_URL?: string;
}

export function resolveApiBaseUrl(env: ApiBaseUrlEnv): string {
  const configuredUrl = env.VITE_API_URL?.trim();
  if (configuredUrl) {
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
  _retry?: boolean;
  _skipAuth?: boolean;
}

type TokenGetter = () => string | null;
type Refresher = () => Promise<string | null>;
type OnAuthFailure = () => void;

let getAccess: TokenGetter = () => null;
let refresh: Refresher = async () => null;
let onAuthFailure: OnAuthFailure = () => {};

export function configureAuthHooks(opts: {
  getAccessToken: TokenGetter;
  refreshTokens: Refresher;
  onAuthFailure: OnAuthFailure;
}): void {
  getAccess = opts.getAccessToken;
  refresh = opts.refreshTokens;
  onAuthFailure = opts.onAuthFailure;
}

api.interceptors.request.use((config) => {
  const cfg = config as RetryConfig;
  if (cfg._skipAuth) return config;
  const token = getAccess();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// In-flight refresh promise — concurrent 401s share the same refresh call so
// we don't burn the refresh token twice and trigger reuse-detection.
let refreshing: Promise<string | null> | null = null;

export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= refresh()
    .then((token) => {
      if (!token) onAuthFailure();
      return token;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetryConfig | undefined;
    if (
      !original ||
      original._skipAuth ||
      original._retry ||
      error.response?.status !== 401
    ) {
      return Promise.reject(error);
    }

    original._retry = true;

    const newAccess = await refreshAccessToken();
    if (!newAccess) {
      return Promise.reject(error);
    }
    original.headers.set("Authorization", `Bearer ${newAccess}`);
    return api.request(original);
  },
);

export function withoutAuth(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...(config ?? {}), _skipAuth: true } as AxiosRequestConfig;
}
