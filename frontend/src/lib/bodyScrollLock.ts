interface ScrollLock {
  count: number;
  original: { property: string; value: string; priority: string }[];
}

const locks = new WeakMap<HTMLElement, ScrollLock>();

/** Keep scrolling locked until the last overlay releases its ownership. */
export function acquireBodyScrollLock(): () => void {
  const body = document.body;
  let lock = locks.get(body);
  if (!lock) {
    lock = {
      count: 0,
      original: ["overflow", "overflow-x", "overflow-y"].map((property) => ({
        property,
        value: body.style.getPropertyValue(property),
        priority: body.style.getPropertyPriority(property),
      })),
    };
    locks.set(body, lock);
    body.style.setProperty("overflow", "hidden");
  }
  lock.count += 1;
  const ownedLock = lock;
  let released = false;

  return () => {
    if (released) return;
    released = true;
    ownedLock.count -= 1;
    if (ownedLock.count > 0) return;

    body.style.removeProperty("overflow");
    for (const { property, value, priority } of ownedLock.original) {
      if (value) body.style.setProperty(property, value, priority);
    }
    locks.delete(body);
  };
}
