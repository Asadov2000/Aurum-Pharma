export const POS_PAYMENT_METHODS = ["cash", "card", "qr"] as const;

export type PosPaymentMethod = (typeof POS_PAYMENT_METHODS)[number];

export const DEFAULT_POS_PAYMENT_METHODS: readonly PosPaymentMethod[] = POS_PAYMENT_METHODS;

export function normalizePosPaymentMethods(
  methods: readonly unknown[] | null | undefined,
): PosPaymentMethod[] {
  if (!methods) return [...DEFAULT_POS_PAYMENT_METHODS];

  const normalized = methods.filter(
    (method, index): method is PosPaymentMethod =>
      POS_PAYMENT_METHODS.includes(method as PosPaymentMethod) &&
      methods.indexOf(method) === index,
  );
  return normalized.length > 0 ? normalized : [...DEFAULT_POS_PAYMENT_METHODS];
}
