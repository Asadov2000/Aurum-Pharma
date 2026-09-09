import { type MouseEvent } from "react";

const interactiveTarget = [
  "a",
  "button",
  "input",
  "select",
  "textarea",
  "label",
  "summary",
  "[role='button']",
  "[role='link']",
  "[role='checkbox']",
  "[role='switch']",
  "[role='menuitem']",
  "[role='option']",
  "[role='combobox']",
  "[role='textbox']",
  "[contenteditable]:not([contenteditable='false'])",
].join(",");

/** Extend a record's native open button to its free surface without stealing other actions. */
export function shouldOpenRecord(event: MouseEvent<HTMLElement>): boolean {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey
  ) {
    return false;
  }
  const target = event.target;
  if (!(target instanceof Element) || target.closest(interactiveTarget)) return false;
  return !event.currentTarget.ownerDocument.getSelection()?.toString();
}
