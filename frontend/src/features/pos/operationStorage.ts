export function readStoredValue(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStoredValue(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key) === value;
  } catch {
    return false;
  }
}

export function removeStoredValue(key: string): boolean {
  try {
    window.localStorage.removeItem(key);
    return window.localStorage.getItem(key) === null;
  } catch {
    return false;
  }
}

export function readStoredJson(key: string): unknown {
  const value = readStoredValue(key);
  if (value === null) return null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    removeStoredValue(key);
    return null;
  }
}

export function writeStoredJson(key: string, value: unknown): boolean {
  return writeStoredValue(key, JSON.stringify(value));
}
