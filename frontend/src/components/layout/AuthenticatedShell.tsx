import { type ReactNode } from "react";

import { AppLayout } from "./AppLayout";
import { RouteAccessGuard } from "./RouteAccessGuard";

// Public auth routes never render the authenticated navigation/preferences shell.
export function AuthenticatedShell({ children }: { children: ReactNode }): JSX.Element {
  return (
    <AppLayout>
      <RouteAccessGuard>{children}</RouteAccessGuard>
    </AppLayout>
  );
}
