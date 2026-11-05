import * as z from "zod/mini";
import en from "zod/v4/locales/en.js";

// Match classic Zod's fallback messages without importing its schema methods.
z.config(en());

export const emailIdentitySchema = z.string().check(
  z.trim(),
  z.minLength(1, "Введите email"),
  z.email("Некорректный email"),
);

export const phoneIdentitySchema = z.pipe(
  z.pipe(
    z.string(),
    z.transform((value) => {
      const compact = value.replace(/[\s()-]/g, "");
      if (/^\d{9}$/.test(compact)) return `+992${compact}`;
      if (/^992\d{9}$/.test(compact)) return `+${compact}`;
      return compact;
    }),
  ),
  z.string().check(z.regex(/^\+992\d{9}$/, "Введите номер +992 и 9 цифр")),
);

export const oneTimeCodeSchema = z.string().check(
  z.trim(),
  z.regex(/^\d{6}$/, "Код состоит из 6 цифр"),
);
export const newPasswordSchema = z.string().check(
  z.minLength(12, "Используйте не менее 12 символов"),
  z.maxLength(128, "Не более 128 символов"),
  z.refine((value) => new TextEncoder().encode(value).length <= 72, {
    message:
      "Сократите пароль: максимум 72 байта — например, 72 латинские или 36 русских букв. Эмодзи занимают больше места.",
  }),
);
