import { type UnitSalePreparation } from "./types";

const quantityNames: Record<UnitSalePreparation["unit"], readonly [string, string, string]> = {
  tablet: ["таблетка", "таблетки", "таблеток"],
  capsule: ["капсула", "капсулы", "капсул"],
  ampoule: ["ампула", "ампулы", "ампул"],
  sachet: ["саше", "саше", "саше"],
  suppository: ["суппозиторий", "суппозитория", "суппозиториев"],
};
const quantityPlural = new Intl.PluralRules("ru");

export function describeQuantity(quantity: number, unit: UnitSalePreparation["unit"]): string {
  const plural = quantityPlural.select(quantity);
  return `${quantity} ${quantityNames[unit][plural === "one" ? 0 : plural === "few" ? 1 : 2]}`;
}
