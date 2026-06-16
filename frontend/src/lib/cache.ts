/**
 * Lightweight TTL cache with stale-while-revalidate semantics.
 *
 * - `get()` returns fresh data (within TTL) or null.
 * - `set()` stores a value with the current timestamp.
 * - `startOf()` returns true if the caller should revalidate in background.
 * - `invalidate()` clears entries matching a prefix (e.g. "/api/deals").
 *
 * Cached entries expire after 60 seconds.
 * Stale entries are eligible for background refresh after 30 seconds.
 */

interface CacheEntry {
  data: unknown;
  ts: number;
}

const store = new Map<string, CacheEntry>();
const TTL = 60_000;       // 60 seconds — discard completely
const STALE_AFTER = 30_000; // 30 seconds — serve but allow background refresh

/** Return cached data if still fresh, else null. */
export function cacheGet<T>(key: string): T | null {
  const entry = store.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > TTL) {
    store.delete(key);
    return null;
  }
  return entry.data as T;
}

/** Store response data under a cache key. */
export function cacheSet(key: string, data: unknown): void {
  store.set(key, { data, ts: Date.now() });
}

/** True when the entry exists but is stale enough to warrant a background refresh. */
export function cacheShouldRefresh(key: string): boolean {
  const entry = store.get(key);
  if (!entry) return true; // nothing cached → must fetch
  return Date.now() - entry.ts > STALE_AFTER;
}

/** Remove all entries whose key starts with `prefix` (useful after a POST/PUT/DELETE). */
export function cacheInvalidate(prefix: string): void {
  const keys = Array.from(store.keys());
  for (const key of keys) {
    if (key.startsWith(prefix)) store.delete(key);
  }
}

/** Clear everything (e.g. on logout). */
export function cacheClear(): void {
  store.clear();
}
