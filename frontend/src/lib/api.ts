import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

const baseURL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

export const api: AxiosInstance = axios.create({
  baseURL,
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

    refreshing ??= refresh().finally(() => {
      refreshing = null;
    });

    const newAccess = await refreshing;
    if (!newAccess) {
      onAuthFailure();
      return Promise.reject(error);
    }
    original.headers.set("Authorization", `Bearer ${newAccess}`);
    return api.request(original);
  },
);

export function withoutAuth(config?: AxiosRequestConfig): AxiosRequestConfig {
  return { ...(config ?? {}), _skipAuth: true } as AxiosRequestConfig;
}
