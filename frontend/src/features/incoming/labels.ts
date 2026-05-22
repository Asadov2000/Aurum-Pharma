import { type IncomingStatus } from "./types";

export const statusLabel: Record<IncomingStatus, string> = {
  draft: "Черновик",
  accepted: "Принят",
  rejected: "Отклонён",
};

export const statusTone: Record<
  IncomingStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  draft: "info",
  accepted: "success",
  rejected: "danger",
};

export const statusOptions: IncomingStatus[] = ["draft", "accepted", "rejected"];
