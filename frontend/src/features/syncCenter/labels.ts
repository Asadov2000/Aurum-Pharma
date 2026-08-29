import {
  type SyncContactState,
  type SyncIntegrityState,
  type SyncMonitoringHealth,
  type SyncMonitoringMode,
  type SyncQuarantineStatus,
} from "./types";

export const healthLabel: Record<SyncMonitoringHealth, string> = {
  healthy: "Стабильно",
  delayed: "Есть задержка",
  offline: "Нет связи",
  critical: "Требует вмешательства",
  revoked: "Отозван",
};

export const healthTone: Record<
  SyncMonitoringHealth,
  "success" | "warning" | "danger" | "neutral"
> = {
  healthy: "success",
  delayed: "warning",
  offline: "danger",
  critical: "danger",
  revoked: "neutral",
};

export const modeLabel: Record<SyncMonitoringMode, string> = {
  shadow_readonly: "Резервное чтение",
  edge_writer: "Локальная запись",
};

export const contactStateLabel: Record<SyncContactState, string> = {
  recent: "Недавнее обращение",
  stale: "Обращение задерживается",
  offline: "Давно не обращался",
  never_seen: "Ещё не подключался",
};

export const integrityStateLabel: Record<SyncIntegrityState, string> = {
  verified: "Проверена",
  stale_report: "Отчёт устарел",
  unverified: "Не подтверждена",
  mismatch: "Обнаружено расхождение",
};

export const integrityTone: Record<
  SyncIntegrityState,
  "success" | "warning" | "danger" | "neutral"
> = {
  verified: "success",
  stale_report: "warning",
  unverified: "neutral",
  mismatch: "danger",
};

export const quarantineStatusLabel: Record<SyncQuarantineStatus, string> = {
  gap: "Пропуск последовательности",
  quarantined: "Остановлено защитой",
  mismatch: "Обнаружено расхождение",
};

const quarantineReasonLabels: Readonly<Record<string, string>> = {
  writer_epoch_mismatch: "Не совпадает версия потока записи",
  response_checkpoint_gap: "Обнаружен пропуск контрольной точки",
  response_checkpoint_mismatch: "Не совпадает контрольная точка",
  event_scope_mismatch: "Событие относится к другому контуру",
  event_identity_collision: "Обнаружен конфликт идентичности события",
  sequence_gap: "Нарушена последовательность событий",
  sale_projection_collision: "Обнаружен конфликт локальной продажи",
  operation_id_collision: "Обнаружен конфликт операции",
  payload_hash_mismatch: "Нарушена целостность данных события",
  payload_envelope_mismatch: "Не совпадают метаданные события",
  source_checksum_mismatch: "Нарушена целостность исходного потока",
  projection_hash_mismatch: "Нарушена целостность локальной проекции",
  projection_checksum_mismatch: "Не совпадает контрольная сумма проекции",
  refund_parent_scope_mismatch: "Возврат связан с другим контуром",
  refund_item_parent_mismatch: "Позиция возврата не совпадает с продажей",
  refund_lifecycle_mismatch: "Нарушена последовательность возврата",
  local_projection_mismatch: "Нарушена целостность локальной проекции",
  legacy_unknown_quarantine: "Причина остановки требует проверки инженера",
};

export function quarantineReasonLabel(reason: string | null): string {
  if (!reason) return "Требуется проверка инженера";
  return quarantineReasonLabels[reason] ?? "Требуется проверка инженера";
}

export function formatDateTime(value: string | null): string {
  if (!value) return "Нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Нет данных";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatLag(value: number | null): string {
  if (value === null) return "Не определено";
  if (value === 0) return "Нет отставания";
  return `${new Intl.NumberFormat("ru-RU").format(value)} ${eventWord(value)}`;
}

function eventWord(value: number): string {
  const absolute = Math.abs(value) % 100;
  const lastDigit = absolute % 10;
  if (absolute > 10 && absolute < 20) return "событий";
  if (lastDigit === 1) return "событие";
  if (lastDigit > 1 && lastDigit < 5) return "события";
  return "событий";
}
