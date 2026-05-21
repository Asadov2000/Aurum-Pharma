import { Button, Card, CardContent, CardHeader, CardTitle } from "@/components/ui";

import { useAuth } from "./hooks";

export function Dashboard(): JSX.Element {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">Aurum Pharma</h1>
          <Button variant="secondary" onClick={() => void logout()}>
            Выйти
          </Button>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Профиль</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-slate-700">
            <p>
              <span className="font-medium">Имя:</span> {user?.full_name}
            </p>
            <p>
              <span className="font-medium">Email:</span> {user?.email}
            </p>
            <p>
              <span className="font-medium">Статус:</span> {user?.status}
            </p>
            {user?.is_developer && <p className="text-emerald-700">Developer</p>}
            {user?.is_administrator && <p className="text-emerald-700">Administrator</p>}
          </CardContent>
        </Card>
        <p className="text-sm text-slate-500">
          Этап 1 — оболочка. Дальнейшие модули появятся по мере реализации.
        </p>
      </div>
    </div>
  );
}
