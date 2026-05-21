import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { z } from "zod";

import { AuthGuard } from "@/features/auth/AuthGuard";
import { Dashboard } from "@/features/auth/Dashboard";
import { LoginPage } from "@/features/auth/LoginPage";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: () => (
    <AuthGuard>
      <Dashboard />
    </AuthGuard>
  ),
});

const loginSearchSchema = z.object({
  from: z.string().optional(),
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: (raw) => loginSearchSchema.parse(raw),
});

const routeTree = rootRoute.addChildren([indexRoute, loginRoute]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
