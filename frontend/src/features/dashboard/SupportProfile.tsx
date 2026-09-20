import { Badge, PageHeader } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";

export default function SupportProfile(): JSX.Element {
  const { user } = useAuth();
  return (
    <div className="space-y-4">
      <PageHeader title="Главная" showTitleOnDesktop />
      <section className="max-w-2xl overflow-hidden rounded-lg border border-border bg-surface">
        <header className="border-b border-border px-5 py-4">
          <h2 className="text-lg font-semibold text-foreground">{user?.full_name ?? "Профиль"}</h2>
          <p className="mt-1 text-sm text-foreground-muted">{user?.email}</p>
        </header>
        <div className="px-5 py-4 text-sm text-foreground-secondary">
          <div className="flex flex-wrap gap-2">
            {user?.is_developer && <Badge tone="info">Разработчик</Badge>}
            {user?.is_administrator && <Badge tone="info">Администратор</Badge>}
          </div>
          <p className="mt-3 text-foreground-muted">
            Сводка по аптеке доступна пользователям, привязанным к тенанту. Выберите раздел в меню
            слева.
          </p>
        </div>
      </section>
    </div>
  );
}
