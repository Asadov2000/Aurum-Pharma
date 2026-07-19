import { Link } from "@tanstack/react-router";

import { type AppRoutePath } from "@/components/layout/routeAccess";
import { Card, CardContent } from "@/components/ui";

/** Friendly "this section isn't for your role" panel. Shared by the dashboard
 *  and protected screens so the wording and layout stay identical. */
export function AccessDeniedCard({
  title,
  message,
  fallbackTo = "/pos",
  fallbackLabel = "Касса",
}: {
  title: string;
  message: string;
  fallbackTo?: AppRoutePath | null;
  fallbackLabel?: string;
}): JSX.Element {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">{title}</h1>
      <Card>
        <CardContent className="space-y-2 py-6 text-sm text-foreground-secondary">
          <p>{message}</p>
          {fallbackTo && (
            <p className="text-foreground-muted">
              Доступный раздел —{" "}
              <Link to={fallbackTo} className="text-primary hover:underline">
                {fallbackLabel}
              </Link>
              .
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
