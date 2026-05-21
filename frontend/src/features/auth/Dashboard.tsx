import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";

import { useAuth } from "./hooks";

export function Dashboard(): JSX.Element {
  const { user } = useAuth();
  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">Профиль</h1>
      <Card>
        <CardHeader>
          <CardTitle>{user?.full_name}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm text-slate-700">
          <p>
            <span className="font-medium">Email:</span> {user?.email}
          </p>
          <p>
            <span className="font-medium">Статус:</span> {user?.status}
          </p>
          {user?.is_developer && <p className="text-emerald-700">Developer</p>}
          {user?.is_administrator && <p className="text-emerald-700">Administrator</p>}
          {user?.home_tenant_id && (
            <p className="text-slate-600">
              <span className="font-medium">Тенант (id):</span>{" "}
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
                {user.home_tenant_id}
              </code>
            </p>
          )}
        </CardContent>
      </Card>
      <p className="text-sm text-slate-500">
        Используй боковое меню для перехода к модулям.
      </p>
    </div>
  );
}
