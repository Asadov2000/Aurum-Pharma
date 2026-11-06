import { z } from "zod";

export const quantitySchema = z
  .string()
  .regex(/^\d{1,11}(?:[.,]\d{1,3})?$/)
  .refine((value) => Number(value.replace(",", ".")) > 0);

/** Display-only catalog estimate. The item.add response remains authoritative. */
export function catalogAmountPreview(price: string | null, quantity: string): string | null {
  if (
    !price ||
    !/^\d{1,12}(?:\.\d{1,2})?$/.test(price) ||
    !quantitySchema.safeParse(quantity).success
  )
    return null;
  const [whole = "0", fraction = ""] = price.split(".");
  const [count = "0", decimals = ""] = quantity.replace(",", ".").split(".");
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  const thousandths = BigInt(count) * 1000n + BigInt(decimals.padEnd(3, "0"));
  const total = (cents * thousandths + 500n) / 1000n;
  if (total > 99_999_999_999_999n) return null;
  return `${(total / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ")},${(total % 100n).toString().padStart(2, "0")}`;
}
