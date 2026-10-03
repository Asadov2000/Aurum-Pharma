import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import {
  applyQuickActions,
  defaultQuickActionPreferences,
  parseQuickActionMenuPreferences,
  QUICK_ACTION_DEFAULTS,
  QUICK_ACTION_DANGER_IDS,
  QUICK_ACTION_SECTIONS,
  QUICK_ACTION_SECTION_LABELS,
  quickActionMenuSchema,
  type QuickActionMenuPreferences,
  type QuickActionSection,
} from "./quickActions";
import { SettingsNotice, SettingsSectionHeader } from "./SettingsPrimitives";
import { useQuickActionPreferences } from "./useQuickActionPreferences";

const LABELS: Record<string, string> = {
  open: "Открыть карточку",
  edit: "Изменить товар",
  copy_name: "Скопировать название",
  copy_barcode: "Скопировать штрихкод",
  archive: "Архивировать",
  restore: "Восстановить из архива",
  copy_number: "Скопировать номер партии",
  profile: "Изменить профиль",
  assign_access: "Настроить доступ",
  reissue_invitation: "Обновить приглашение",
  activate: "Возобновить",
  revoke_sessions: "Завершить сеансы",
  suspend: "Приостановить",
  offboard: "Уволить / отозвать приглашение",
  transfer_ownership: "Передать владение",
};

export function QuickActionsSettingsPanel({
  initialSection = "catalog",
}: {
  initialSection?: QuickActionSection;
}): JSX.Element {
  const [section, setSection] = useState<QuickActionSection>(initialSection);
  const preferences = useQuickActionPreferences(section);
  return (
    <div className="space-y-5">
      <SettingsSectionHeader
        title="Быстрые действия"
        description="Выберите нужные команды и расположите частые выше. Это личные настройки вашего аккаунта."
      />
      <nav aria-label="Раздел быстрых действий" className="flex flex-wrap gap-2">
        {QUICK_ACTION_SECTIONS.map((id) => (
          <Button
            key={id}
            type="button"
            variant={section === id ? "primary" : "secondary"}
            aria-pressed={section === id}
            onClick={() => setSection(id)}
          >
            {QUICK_ACTION_SECTION_LABELS[id]}
          </Button>
        ))}
      </nav>
      <QuickActionsForm
        key={`${preferences.scope}:${section}`}
        section={section}
        preferences={preferences}
      />
    </div>
  );
}

function QuickActionsForm({
  section,
  preferences,
}: {
  section: QuickActionSection;
  preferences: ReturnType<typeof useQuickActionPreferences>;
}): JSX.Element {
  const form = useForm<QuickActionMenuPreferences>({ defaultValues: preferences.config });
  const draft = form.watch();
  const {
    reset,
    setValue,
    formState: { isDirty },
  } = form;
  const saved = JSON.stringify(preferences.config);
  useEffect(() => {
    if (!isDirty) reset(JSON.parse(saved) as QuickActionMenuPreferences);
  }, [isDirty, reset, saved]);
  const allItems = QUICK_ACTION_DEFAULTS[section].order.map((id) => ({
    id,
    label:
      id === "copy_number" && section === "incoming"
        ? "Скопировать номер документа"
        : id === "open" && section === "incoming"
          ? "Открыть документ"
          : (LABELS[id] ?? id),
    tone: QUICK_ACTION_DANGER_IDS.has(id) ? ("danger" as const) : ("default" as const),
  }));
  const ordered = applyQuickActions(allItems, { ...draft, hidden: [] });
  const preview = applyQuickActions(allItems, draft);
  const normal = ordered.filter((item) => item.tone !== "danger");

  const move = (id: string, direction: -1 | 1) => {
    const ids = normal.map((item) => item.id);
    const index = ids.indexOf(id);
    const other = index + direction;
    const current = ids[index];
    const adjacent = ids[other];
    if (current === undefined || adjacent === undefined) return;
    ids[index] = adjacent;
    ids[other] = current;
    setValue(
      "order",
      [...ids, ...ordered.filter((item) => item.tone === "danger").map((item) => item.id)],
      { shouldDirty: true },
    );
  };

  const submit = form.handleSubmit((values) => {
    const checked = quickActionMenuSchema.safeParse(values);
    if (!checked.success) return;
    const config = parseQuickActionMenuPreferences(checked.data, section);
    if (!config) return;
    preferences.save(config);
    reset(config);
  });

  return (
    <form onSubmit={submit} className="space-y-5">
      <div className="flex items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <p className="text-sm font-medium">Меню по правой кнопке</p>
          <p className="mt-1 text-xs text-foreground-muted">Кнопка ⋯ доступна при любом выборе.</p>
        </div>
        <input
          type="checkbox"
          aria-label="Меню по правой кнопке"
          {...form.register("enabled")}
          className="h-5 w-5 accent-primary"
        />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(13rem,0.7fr)]">
        <section aria-label="Порядок действий" className="space-y-3">
          <div className="flex justify-between gap-2">
            <h3 className="text-sm font-semibold">Мой порядок действий</h3>
            <span className="text-xs text-foreground-muted">Видимых: {preview.length}</span>
          </div>
          <div className="divide-y divide-border rounded-md border border-border">
            {ordered.map((item) => (
              <div key={item.id} className="flex min-h-12 items-center gap-3 px-3 py-2">
                <label className="flex min-w-0 flex-1 items-center gap-3 text-sm">
                  <input
                    type="checkbox"
                    aria-label={`Показывать в меню: ${item.label}`}
                    checked={!draft.hidden.includes(item.id)}
                    onChange={(event) =>
                      setValue(
                        "hidden",
                        event.target.checked
                          ? draft.hidden.filter((id) => id !== item.id)
                          : [...draft.hidden, item.id],
                        { shouldDirty: true },
                      )
                    }
                    className="h-4 w-4 accent-primary"
                  />
                  <span>{item.label}</span>
                </label>
                {item.tone === "danger" ? (
                  <span className="text-xs text-foreground-muted">Внизу меню</span>
                ) : (
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={`Выше: ${item.label}`}
                      disabled={normal[0]?.id === item.id}
                      onClick={() => move(item.id, -1)}
                    >
                      ↑
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={`Ниже: ${item.label}`}
                      disabled={normal.at(-1)?.id === item.id}
                      onClick={() => move(item.id, 1)}
                    >
                      ↓
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              const defaults = defaultQuickActionPreferences(section);
              setValue("enabled", defaults.enabled, { shouldDirty: true });
              setValue("order", defaults.order, { shouldDirty: true });
              setValue("hidden", defaults.hidden, { shouldDirty: true });
            }}
          >
            Вернуть стандартные настройки
          </Button>
        </section>
        <aside className="rounded-md bg-background p-4" aria-label="Предпросмотр быстрых действий">
          <p className="mb-3 text-xs font-semibold uppercase text-foreground-muted">Предпросмотр</p>
          <p className="mb-3 text-sm">Так будет выглядеть меню</p>
          <ul className="rounded-md border border-border bg-surface p-1 shadow-sm">
            {preview.map((item) => (
              <li
                key={item.id}
                className={cn(
                  "px-3 py-2 text-sm",
                  item.tone === "danger" && "border-t border-border text-danger-foreground",
                )}
              >
                {item.label}
              </li>
            ))}
            {preview.length === 0 ? (
              <li className="px-3 py-2 text-sm text-foreground-muted">Все команды скрыты</li>
            ) : null}
          </ul>
          <p className="mt-3 text-xs leading-5 text-foreground-muted">
            В меню появятся только команды, доступные вашему аккаунту и подходящие для текущего
            состояния записи. Настройки не расширяют ваши права.
          </p>
        </aside>
      </div>
      {preferences.hasPending || preferences.error ? (
        <SettingsNotice tone="warning">
          Сохранено на этом устройстве. Настройки синхронизируются с аккаунтом после восстановления
          связи.
        </SettingsNotice>
      ) : null}
      {preview.length === 0 ? (
        <SettingsNotice tone="warning">
          Оставьте хотя бы одну команду видимой, чтобы сохранить настройки.
        </SettingsNotice>
      ) : null}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <span className="text-xs text-foreground-muted">
          {isDirty ? "Изменения ещё не применены" : "Настройки применены"}
        </span>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            disabled={!isDirty}
            onClick={() => reset(preferences.config)}
          >
            Отмена
          </Button>
          <Button type="submit" disabled={!isDirty || preview.length === 0}>
            Сохранить настройки
          </Button>
        </div>
      </footer>
    </form>
  );
}
