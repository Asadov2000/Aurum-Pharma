import { type BarcodeType, type DispensingType, type StorageType } from "./types";

export const dispensingLabel: Record<DispensingType, string> = {
  prescription: "По рецепту",
  otc: "Без рецепта",
  special: "Особый порядок отпуска",
};

export const storageLabel: Record<StorageType, string> = {
  normal: "Обычные условия",
  cold: "Хранить в холодильнике",
  frozen: "Хранить в морозильнике",
};

export const barcodeLabel: Record<BarcodeType, string> = {
  ean13: "EAN-13",
  ean8: "EAN-8",
  gs1_128: "GS1-128",
  code128: "Code 128",
  qr: "QR",
  other: "Другое",
};

export const dispensingOptions: DispensingType[] = ["prescription", "otc", "special"];
export const storageOptions: StorageType[] = ["normal", "cold", "frozen"];
export const barcodeOptions: BarcodeType[] = ["ean13", "ean8", "gs1_128", "code128", "qr", "other"];
