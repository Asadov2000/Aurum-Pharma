import { type CustomerReturnDispositionType, type CustomerReturnReasonCode } from "./types";

export const physicalDispositionLabels: Record<CustomerReturnDispositionType, string> = {
  disposed: "Утилизировано",
  supplier_claim: "Возвращено поставщику",
  regulatory_transfer: "Передано уполномоченной организации",
};
export const physicalDispositionReasons: Record<CustomerReturnReasonCode, string> = {
  damaged: "Повреждение",
  quality_issue: "Проблема качества",
  wrong_item: "Ошибочно выданный товар",
  expired: "Истёк срок годности",
  other: "Другое",
};
