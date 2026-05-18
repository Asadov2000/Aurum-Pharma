import axios, { type AxiosInstance } from "axios";

const baseURL = import.meta.env.VITE_API_URL ?? "/api/v1";

export const api: AxiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => Promise.reject(error),
);
