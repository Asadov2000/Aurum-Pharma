export const SETTINGS_SECTION_IDS = [
  "account",
  "interface",
  "menu",
  "notifications",
  "security",
  "device",
  "pharmacy",
  "sales",
  "inventory",
  "reports",
] as const;

export type SettingsSectionId = (typeof SETTINGS_SECTION_IDS)[number];

export function parseSettingsSearch(raw: Record<string, unknown>): {
  section?: SettingsSectionId;
} {
  const section = raw.section;
  return typeof section === "string" && SETTINGS_SECTION_IDS.includes(section as SettingsSectionId)
    ? { section: section as SettingsSectionId }
    : {};
}
