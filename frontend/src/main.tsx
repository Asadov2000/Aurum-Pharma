import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import { bootstrapAuth } from "@/features/auth/bootstrap";
import { registerPwaServiceWorker } from "@/lib/pwa";
import { queryClient } from "@/lib/query";
import { applyRuntimeSurfaceAttribute } from "@/lib/runtime";
import { applyTheme, getThemePreference, watchSystemTheme } from "@/lib/theme";
import { router } from "@/router";
import "@/styles/index.css";

bootstrapAuth();

// The inline script in index.html set the initial theme pre-paint; re-apply
// from the stored preference and keep "system" in sync with the OS.
applyTheme(getThemePreference());
watchSystemTheme();
applyRuntimeSurfaceAttribute();
registerPwaServiceWorker();

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element not found");
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
