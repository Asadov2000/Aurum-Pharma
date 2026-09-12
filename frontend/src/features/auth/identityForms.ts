import { z } from "zod";

export const emailIdentitySchema = z
  .string()
  .trim()
  .min(1, "Введите email")
  .email("Некорректный email");

export const phoneIdentitySchema = z
  .string()
  .transform((value) => {
    const compact = value.replace(/[\s()-]/g, "");
    if (/^\d{9}$/.test(compact)) return `+992${compact}`;
    if (/^992\d{9}$/.test(compact)) return `+${compact}`;
    return compact;
  })
  .pipe(z.string().regex(/^\+992\d{9}$/, "Введите номер +992 и 9 цифр"));

export const oneTimeCodeSchema = z
  .string()
  .trim()
  .regex(/^\d{6}$/, "Код состоит из 6 цифр");
export const newPasswordSchema = z
  .string()
  .min(12, "Используйте не менее 12 символов")
  .max(128, "Не более 128 символов")
  .refine((value) => new TextEncoder().encode(value).length <= 72, {
    message:
      "Сократите пароль: максимум 72 байта — например, 72 латинские или 36 русских букв. Эмодзи занимают больше места.",
  });
