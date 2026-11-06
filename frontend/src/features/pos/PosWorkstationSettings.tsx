import { useEffect, useState } from "react";

import { Button, Modal, SegmentedControl, Switch } from "@/components/ui";
import { applyUserPreferences } from "@/features/settings/appearance";
import { usePreferenceAutosave } from "@/features/settings/usePreferenceAutosave";
import { getThemePreference, type ThemePreference } from "@/lib/theme";
import { setThemePreferenceWithTransition } from "@/lib/themeTransition";

import { ModeToggle } from "./ModeToggle";
import { type PosModePref } from "./usePosMode";

const themes = [
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
  { value: "system", label: "Система" },
] as const;

export function PosWorkstationSettings({
  open,
  onClose,
  pref,
  setPref,
  touch,
  soundOn,
  onSoundChange,
}: {
  open: boolean;
  onClose: () => void;
  pref: PosModePref;
  setPref: (pref: PosModePref) => void;
  touch: boolean;
  soundOn: boolean;
  onSoundChange: (enabled: boolean) => void;
}): JSX.Element {
  const [theme, setTheme] = useState<ThemePreference>(() => getThemePreference());
  const autosave = usePreferenceAutosave("pos-workstation-theme");
  const preferences = autosave.preferences;

  useEffect(() => {
    if (!preferences.data || autosave.hasPending) return;
    applyUserPreferences(preferences.data);
    setTheme(preferences.data.theme);
  }, [autosave.hasPending, preferences.data]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Настройки рабочего места"
      className="max-w-lg"
      footer={
        <Button onClick={onClose} size={touch ? "lg" : "md"}>
          Готово
        </Button>
      }
    >
      <div className="space-y-5">
        <ModeToggle pref={pref} setPref={setPref} touch={touch} />
        <Switch
          label="Звук сканера"
          checked={soundOn}
          className={touch ? "min-h-12" : undefined}
          onChange={(event) => onSoundChange(event.target.checked)}
        />
        <div>
          <p className="mb-2 text-sm font-medium text-foreground">Тема оформления</p>
          <SegmentedControl
            value={theme}
            options={themes}
            label="Тема оформления"
            size={touch ? "lg" : "md"}
            onChange={(value) => {
              setThemePreferenceWithTransition(value);
              setTheme(value);
              autosave.enqueue({ theme: value });
            }}
          />
        </div>
        {preferences.error || autosave.error ? (
          <div
            role="status"
            className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm text-warning-foreground"
          >
            Тема применена в этом браузере, но пока не синхронизирована с аккаунтом.
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={() => void autosave.retry()}
            >
              Повторить синхронизацию
            </Button>
          </div>
        ) : autosave.hasPending ? (
          <p role="status" className="text-xs text-foreground-muted">
            Синхронизация темы…
          </p>
        ) : null}
        <p className="text-xs leading-relaxed text-foreground-muted">
          Режим и звук сохраняются на этом устройстве. Тема относится к вашим личным настройкам.
          Изменение настроек не закрывает смену и не очищает текущий чек.
        </p>
      </div>
    </Modal>
  );
}
