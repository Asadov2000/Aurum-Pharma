import {
  QUICK_ACTION_DANGER_IDS,
  QUICK_ACTION_SECTION_LABELS,
  type QuickActionSection,
} from "@/features/settings/quickActions";

const paths = {
  open: "M9 5H5v14h14v-4M13 5h6v6M19 5 10 14",
  edit: "m4 16 12-12 4 4-12 12H4v-4M13 7l4 4",
  copy: "M9 9h11v11H9zM15 9V4H4v11h5",
  plus: "M5 12h14M12 5v14",
  minus: "M5 12h14",
  remove: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5",
  access: "M12 3 4 6v6c0 4 8 9 8 9s8-5 8-9V6zM8 12l3 3 5-6",
  person: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-3c0-6 16-6 16 0v3",
  box: "m3 7 9-4 9 4-9 4zM3 7v10l9 4 9-4V7M12 11v10",
  pill: "m9 4-5 5a7 7 0 0 0 11 11l5-5A7 7 0 0 0 9 4M7 7l10 10",
  receipt: "M5 3h14v18l-3-2-4 2-4-2-3 2zM8 8h8M8 12h8",
};
type IconName = keyof typeof paths;

function getIcon(name: IconName): JSX.Element {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" className="record-action-icon">
      <path
        d={paths[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function getRecordActionPresentation(id: string): { group?: string; icon: JSX.Element } {
  const copying = id.startsWith("copy_");
  const quantity = id === "increase_qty" || id === "decrease_qty";
  const name: IconName = copying
    ? "copy"
    : id === "increase_qty"
      ? "plus"
      : id === "decrease_qty"
        ? "minus"
        : id === "remove" || id === "archive"
          ? "remove"
          : id === "edit"
            ? "edit"
            : id === "open"
              ? "open"
              : id === "profile"
                ? "person"
                : "access";
  return {
    icon: getIcon(name),
    group: QUICK_ACTION_DANGER_IDS.has(id)
      ? undefined
      : copying
        ? "Копирование"
        : quantity
          ? "Количество"
          : "Действия",
  };
}

export function getRecordContextIcon(section: QuickActionSection): JSX.Element {
  return getIcon(
    section === "employees"
      ? "person"
      : section === "receipts" || section === "pos_cart"
        ? "receipt"
        : section === "catalog"
          ? "pill"
          : "box",
  );
}

export const getRecordSectionLabel = (section: QuickActionSection): string =>
  QUICK_ACTION_SECTION_LABELS[section];
