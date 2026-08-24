import { useEffect, useState } from "react";

import { Button, ConfirmDialog, SegmentedControl, Switch } from "@/components/ui";
import { useDevicePreferences } from "@/lib/devicePreferences";

import { SettingRow, SettingsNotice, SettingsSectionHeader } from "./SettingsPrimitives";

const posModeOptions = [
  { value: "auto", label: "Авто" },
  { value: "keyboard", label: "Клавиатура" },
  { value: "touch", label: "Сенсор" },
] as const;

const receiptWidthOptions = [
  { value: "58", label: "58 мм" },
  { value: "80", label: "80 мм" },
  { value: "A4", label: "A4" },
] as const;

export function DeviceSettingsPanel(): JSX.Element {
  const { preferences, updatePreferences, resetPreferences } = useDevicePreferences();
  const [online, setOnline] = useState(() => navigator.onLine);
  const [soundError, setSoundError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  const testSound = async () => {
    setSoundError(null);
    try {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.12);
      oscillator.addEventListener("ended", () => void context.close(), { once: true });
    } catch {
      setSoundError("Браузер не разрешил воспроизвести тестовый звук.");
    }
  };

  return (
    <div className="space-y-4">
      <SettingsSectionHeader
        title="Касса и оборудование"
        description="Эти параметры относятся только к текущему браузеру и не изменяют другие кассы."
        deviceOnly
        trailing={<span className="text-xs text-foreground-muted">Сохраняется автоматически</span>}
      />

      <div>
        <h3 className="border-b border-border pb-2 text-sm font-semibold text-foreground">
          Рабочее место
        </h3>
        <SettingRow
          title="Режим управления"
          description="Авто выбирает крупные элементы на сенсорном экране."
        >
          <SegmentedControl
            value={preferences.posMode}
            options={posModeOptions}
            label="Режим управления кассой"
            className="grid w-full grid-cols-3 sm:w-[28rem]"
            onChange={(posMode) => updatePreferences({ posMode })}
          />
        </SettingRow>
      </div>

      <div>
        <h3 className="border-b border-border pb-2 text-sm font-semibold text-foreground">
          Сканер штрихкодов
        </h3>
        <SettingRow title="Звук успешного сканирования">
          <div className="flex flex-wrap items-center justify-end gap-3">
            <Switch
              label={preferences.scannerSound ? "Включён" : "Выключен"}
              checked={preferences.scannerSound}
              onChange={(event) => updatePreferences({ scannerSound: event.target.checked })}
            />
            <Button type="button" variant="secondary" size="sm" onClick={() => void testSound()}>
              Проверить звук
            </Button>
          </div>
        </SettingRow>
        {soundError ? <SettingsNotice tone="warning">{soundError}</SettingsNotice> : null}
      </div>

      <div>
        <h3 className="border-b border-border pb-2 text-sm font-semibold text-foreground">
          Печать чеков
        </h3>
        <SettingRow
          title="Ширина бумаги по умолчанию"
          description="В окне печати конкретной кассы можно выбрать другой формат."
        >
          <SegmentedControl
            value={preferences.receiptWidth}
            options={receiptWidthOptions}
            label="Ширина бумаги"
            className="grid w-full grid-cols-3 sm:w-[24rem]"
            onChange={(receiptWidth) => updatePreferences({ receiptWidth })}
          />
        </SettingRow>
      </div>

      <div>
        <h3 className="border-b border-border pb-2 text-sm font-semibold text-foreground">
          Соединение
        </h3>
        <SettingRow
          title="Состояние браузера"
          description="Это проверка сети устройства, а не подтверждение связи с сервером Aurum."
        >
          <span
            className={
              online
                ? "inline-flex items-center gap-2 text-sm font-medium text-success-foreground"
                : "inline-flex items-center gap-2 text-sm font-medium text-warning-foreground"
            }
          >
            <span
              aria-hidden="true"
              className={`h-2.5 w-2.5 rounded-full ${online ? "bg-success" : "bg-warning"}`}
            />
            {online ? "Устройство в сети" : "Нет подключения к сети"}
          </span>
        </SettingRow>
      </div>

      <div className="flex justify-end border-t border-border pt-4">
        <Button type="button" variant="secondary" onClick={() => setConfirmReset(true)}>
          Сбросить настройки устройства
        </Button>
      </div>

      <ConfirmDialog
        open={confirmReset}
        title="Сбросить настройки устройства?"
        message="Режим кассы, звук и формат чека вернутся к значениям по умолчанию. Продажи и черновики чеков не будут удалены."
        confirmLabel="Сбросить"
        variant="danger"
        onConfirm={() => {
          resetPreferences();
          setConfirmReset(false);
        }}
        onCancel={() => setConfirmReset(false)}
      />
    </div>
  );
}
