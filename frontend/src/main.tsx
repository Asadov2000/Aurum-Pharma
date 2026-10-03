import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import { bootstrapAuth } from "@/features/auth/bootstrap";
import { ConnectivityProvider } from "@/lib/connectivity";
import { applyDensity, getDensityPreference } from "@/lib/density";
import { notifyDesktopReady } from "@/lib/desktopBridge";
import { registerPwaServiceWorker } from "@/lib/pwa";
import { queryClient } from "@/lib/query";
import { applyRuntimeSurfaceAttribute } from "@/lib/runtime";
import { applyTheme, getThemePreference, watchSystemTheme } from "@/lib/theme";
import { router } from "@/router";
import "@/styles/index.css";
import "@/components/ui/Switch.css";

bootstrapAuth();

// The inline script in index.html set the initial theme pre-paint; re-apply
// from the stored preference and keep "system" in sync with the OS.
applyTheme(getThemePreference());
watchSystemTheme();
applyDensity(getDensityPreference());
applyRuntimeSurfaceAttribute();
registerPwaServiceWorker();

const application = (
  <QueryClientProvider client={queryClient}>
    <ConnectivityProvider>
      <RouterProvider router={router} />
    </ConnectivityProvider>
  </QueryClientProvider>
);
const renderedApplication = import.meta.env.DEV ? (
  <React.StrictMode>{application}</React.StrictMode>
) : (
  application
);

createRoot(document.getElementById("root")!).render(renderedApplication);

notifyDesktopReady();
