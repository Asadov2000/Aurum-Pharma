import { api } from "@/lib/api";

export async function requiresSalePrescription(saleId: string): Promise<boolean> {
  const { data } = await api.get<unknown>(
    `/sales/${encodeURIComponent(saleId)}/physical-cart/dispensing-preflight`,
    { timeout: 8_000 },
  );
  if (
    typeof data !== "object" ||
    data === null ||
    !("sale_id" in data) ||
    data.sale_id !== saleId ||
    !("requires_prescription" in data) ||
    typeof data.requires_prescription !== "boolean"
  ) {
    throw new Error("Не удалось проверить условия отпуска. Оплата ещё не начата.");
  }
  return data.requires_prescription;
}
