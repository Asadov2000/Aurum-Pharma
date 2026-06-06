import { Link } from "@tanstack/react-router";

import { Card, CardContent } from "@/components/ui";

/** Friendly "this section isn't for your role" panel. Shared by the dashboard
 *  and the team-management screens (Пользователи / Роли) so the wording and
 *  layout stay identical. A seller's home section is the POS, so we always
 *  point there. */
export function AccessDeniedCard({
  title,
  message,
}: {
  title: string;
  message: string;
}): JSX.Element {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">{title}</h1>
      <Card>
        <CardContent className="space-y-2 py-6 text-sm text-foreground-secondary">
          <p>{message}</p>
          <p className="text-foreground-muted">
            Ваш раздел —{" "}
            <Link to="/pos" className="text-primary hover:underline">
              Касса
            </Link>
            .
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
