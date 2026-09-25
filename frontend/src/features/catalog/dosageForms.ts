/** Common pharmacy choices first; uncommon or imported values remain editable. */
export const popularDosageForms = [
  "Таблетки",
  "Капсулы",
  "Сироп",
  "Раствор",
  "Суспензия",
  "Капли",
  "Мазь",
  "Крем",
  "Гель",
  "Спрей",
  "Порошок",
  "Суппозитории",
] as const;

export const otherDosageForms = [
  "Аэрозоль",
  "Газ медицинский",
  "Гранулы",
  "Драже",
  "Капли глазные",
  "Капли назальные",
  "Капли ушные",
  "Концентрат для приготовления раствора",
  "Лиофилизат для приготовления раствора",
  "Лосьон",
  "Настойка",
  "Паста",
  "Пена",
  "Пластырь трансдермальный",
  "Порошок для приготовления раствора",
  "Порошок для приготовления суспензии",
  "Раствор для ингаляций",
  "Раствор для инъекций",
  "Раствор для инфузий",
  "Раствор для наружного применения",
  "Раствор для полоскания",
  "Спрей назальный",
  "Суппозитории вагинальные",
  "Суппозитории ректальные",
  "Таблетки для рассасывания",
  "Таблетки диспергируемые",
  "Таблетки жевательные",
  "Таблетки покрытые оболочкой",
  "Таблетки шипучие",
  "Шампунь",
  "Эмульсия",
] as const;

export function presetDosageForm(value: string | null | undefined): string | null {
  const normalized = value?.trim().toLocaleLowerCase("ru-RU");
  if (!normalized) return null;
  const option = [...popularDosageForms, ...otherDosageForms].find(
    (form) => form.toLocaleLowerCase("ru-RU") === normalized,
  );
  return option?.toLocaleLowerCase("ru-RU") ?? null;
}
