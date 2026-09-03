import "@testing-library/jest-dom";

/*
 * Node 22 exposes an experimental built-in `localStorage` global (this is the
 * source of the "--localstorage-file was provided without a valid path"
 * warning under Vitest). That global shadows jsdom's storage on `globalThis`
 * and is missing parts of the Storage API such as `clear()`, which made the
 * TopBar theme tests fail with "localStorage.clear is not a function".
 *
 * Guarantee a spec-complete web Storage on both `window` and `globalThis`,
 * preferring jsdom's own implementation (so `Storage.prototype` spies still
 * apply) and falling back to a small in-memory Storage only if needed.
 */
function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  } as Storage;
}

function ensureStorage(name: "localStorage" | "sessionStorage") {
  const current = (globalThis as Record<string, unknown>)[name] as
    | Storage
    | undefined;
  if (current && typeof current.clear === "function") return;

  const fromWindow =
    typeof window !== "undefined"
      ? ((window as unknown as Record<string, unknown>)[name] as
          | Storage
          | undefined)
      : undefined;

  const storage =
    fromWindow && typeof fromWindow.clear === "function"
      ? fromWindow
      : createMemoryStorage();

  for (const target of [
    globalThis,
    typeof window !== "undefined" ? window : undefined,
  ]) {
    if (!target) continue;
    try {
      Object.defineProperty(target, name, {
        configurable: true,
        value: storage,
      });
    } catch {
      /* leave a locked-down global as-is */
    }
  }
}

ensureStorage("localStorage");
ensureStorage("sessionStorage");
