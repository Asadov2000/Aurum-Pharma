import { useEffect, useState } from "react";

import { Button, SegmentedControl, Switch } from "@/components/ui";
import { getDensityPreference, setDensityPreference } from "@/lib/density";
import { getThemePreference, setThemePreference } from "@/lib/theme";

import { applyLocalAppearance, applyUserPreferences, getLocalAppearance } from "./appearance";
import { SettingRow, SettingsNotice, SettingsSectionHeader } from "./SettingsPrimitives";
import {
  type AccentPreference,
  type ContrastPreference,
  type DensityPreference,
  type ThemePreference,
  type UserPreferencesUpdate,
} from "./types";
import { usePreferenceAutosave } from "./usePreferenceAutosave";

const themeOptions = [
  { value: "system", label: "Система" },
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
] as const;

const densityOptions = [
  { value: "auto", label: "Авто" },
  { value: "compact", label: "Компактно" },
  { value: "comfortable", label: "Обычно" },
  { value: "touch", label: "Сенсор" },
] as const;

const contrastOptions = [
  { value: "standard", label: "Стандартная" },
  { value: "high", label: "Повышенная" },
] as const;

const accents: ReadonlyArray<{ value: AccentPreference; label: string; color: string }> = [
  { value: "teal", label: "Бирюзовый", color: "bg-[#05657b]" },
  { value: "blue", label: "Синий", color: "bg-[#2766d7]" },
  { value: "violet", label: "Фиолетовый", color: "bg-[#7652d6]" },
  { value: "green", label: "Зелёный", color: "bg-[#18945a]" },
  { value: "amber", label: "Янтарный", color: "bg-[#dc8a09]" },
  { value: "rose", label: "Розовый", color: "bg-[#c9365a]" },
];

export function InterfaceSettingsPanel(): JSX.Element {
  const autosave = usePreferenceAutosave("settings-interface");
  const preferences = autosave.preferences;
  const local = getLocalAppearance();
  const [theme, setTheme] = useState<ThemePreference>(() => getThemePreference());
  const [density, setDensity] = useState<DensityPreference>(() => getDensityPreference());
  const [contrast, setContrast] = useState<ContrastPreference>(local.contrast);
  const [reduceMotion, setReduceMotion] = useState(local.reduceMotion);
  const [accent, setAccent] = useState<AccentPreference>(local.accent);

  useEffect(() => {
    if (!preferences.data || autosave.hasPending) return;
    applyUserPreferences(preferences.data);
    setTheme(preferences.data.theme);
    setDensity(preferences.data.density);
    setContrast(preferences.data.contrast);
    setReduceMotion(preferences.data.reduce_motion);
    setAccent(preferences.data.accent);
  }, [autosave.hasPending, preferences.data]);

  const sync = (patch: Omit<UserPreferencesUpdate, "expected_version">) => {
    if (!preferences.data) return;
    autosave.enqueue(patch);
  };

  const changeTheme = (value: ThemePreference) => {
    setThemePreference(value);
    setTheme(value);
    sync({ theme: value });
  };

  const changeDensity = (value: DensityPreference) => {
    setDensityPreference(value);
    setDensity(value);
    sync({ density: value });
  };

  const applyAccessibility = (
    next: Partial<{
      contrast: ContrastPreference;
      reduceMotion: boolean;
      accent: AccentPreference;
    }>,
  ) => {
    const value = {
      contrast: next.contrast ?? contrast,
      reduceMotion: next.reduceMotion ?? reduceMotion,
      accent: next.accent ?? accent,
    };
    setContrast(value.contrast);
    setReduceMotion(value.reduceMotion);
    setAccent(value.accent);
    applyLocalAppearance(value);
    sync({
      ...(next.contrast ? { contrast: next.contrast } : {}),
      ...(next.reduceMotion !== undefined ? { reduce_motion: next.reduceMotion } : {}),
      ...(next.accent ? { accent: next.accent } : {}),
    });
  };

  return (
    <div className="space-y-4">
      <SettingsSectionHeader
        title="Интерфейс"
        description="Оформление применяется сразу и синхронизируется между вашими устройствами."
        trailing={
          <span className="text-xs text-foreground-muted">
            {autosave.hasPending ? "Синхронизация…" : "Сохраняется автоматически"}
          </span>
        }
      />

      {preferences.error ? (
        <SettingsNotice tone="warning">
          Сервер временно недоступен. Изменения применены в этом браузере и будут доступны для
          повторной синхронизации.
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-2"
            onClick={() => void autosave.retry()}
          >
            Повторить
          </Button>
        </SettingsNotice>
      ) : null}
      {autosave.error ? (
        <SettingsNotice tone="warning">
          Настройка применена локально, но пока не синхронизирована с аккаунтом.
        </SettingsNotice>
      ) : null}

      <div>
        <SettingRow title="Тема оформления">
          <SegmentedControl
            value={theme}
            options={themeOptions}
            label="Тема оформления"
            className="grid w-full min-w-0 grid-cols-3 sm:w-[25rem]"
            onChange={changeTheme}
          />
        </SettingRow>
        <SettingRow
          title="Размер элементов"
          description="Авто включает сенсорный режим только на устройствах с крупным указателем."
        >
          <SegmentedControl
            value={density}
            options={densityOptions}
            label="Размер элементов"
            className="grid w-full min-w-0 grid-cols-2 sm:w-[31rem] sm:grid-cols-4"
            onChange={changeDensity}
          />
        </SettingRow>
        <SettingRow title="Контрастность">
          <SegmentedControl
            value={contrast}
            options={contrastOptions}
            label="Контрастность"
            className="grid w-full min-w-0 grid-cols-2 sm:w-[21rem]"
            onChange={(value) => applyAccessibility({ contrast: value })}
          />
        </SettingRow>
        <SettingRow title="Анимация">
          <Switch
            label="Уменьшать движение"
            checked={reduceMotion}
            onChange={(event) => applyAccessibility({ reduceMotion: event.target.checked })}
          />
        </SettingRow>
        <SettingRow title="Цветовой акцент">
          <div className="flex flex-wrap justify-start gap-2 sm:gap-3 md:justify-end">
            {accents.map((item) => (
              <button
                key={item.value}
                type="button"
                aria-label={item.label}
                aria-pressed={accent === item.value}
                title={item.label}
                onClick={() => applyAccessibility({ accent: item.value })}
                className={`h-11 w-11 rounded-md border-2 ${item.color} ${
                  accent === item.value
                    ? "border-foreground ring-2 ring-ring ring-offset-2 ring-offset-surface"
                    : "border-transparent"
                }`}
              >
                <span className="sr-only">{item.label}</span>
              </button>
            ))}
          </div>
        </SettingRow>
      </div>

      <div className="rounded-md border border-border bg-background p-4">
        <p className="text-xs font-medium uppercase text-foreground-muted">Предпросмотр</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-center">
          <div className="rounded-md bg-foreground/[0.04] px-3 py-2 text-sm font-medium">
            Каталог
          </div>
          <div className="h-[var(--control-height-md)] rounded-md border border-input bg-surface px-3 py-2 text-sm text-foreground-muted">
            Введите название
          </div>
          <span className="inline-flex h-[var(--control-height-md)] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground">
            Создать позицию
          </span>
        </div>
      </div>
    </div>
  );
}
