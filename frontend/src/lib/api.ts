import {
  cacheGet,
  cacheSet,
  cacheShouldRefresh,
  cacheInvalidate,
} from "./cache";

/**
 * Shared fetch wrapper with error serialization and TTL caching.
 *
 * GET responses are cached for 60 s. Navigating back to a previously visited
 * page serves cached data instantly while refreshing in the background.
 *
 * Mutation verbs (POST / PUT / DELETE) invalidate the matching cache prefix
 * so the next GET returns fresh data.
 *
 * Usage:
 *   import { apiFetch } from "@/lib/api";
 *   const data = await apiFetch<Deal[]>("/api/deals");
 */
export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  // Only GET responses are cached — store under the raw URL so
  // cacheInvalidate(url) from a mutation branch always matches.
  const cacheKey = url;

  // ── Mutations: send & invalidate cache ────────────────────────────
  if (method !== "GET") {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Request failed (${res.status})`);
    }
    // Strip query-string so /api/buyers invalidates /api/buyers?foo=bar
    const prefix = url.split("?")[0];
    cacheInvalidate(prefix);
    cacheInvalidate(prefix + "?"); // Also clear paginated variants
    if (res.status === 204) return undefined as T;
    return res.json();
  }

  // ── GET: serve from cache if fresh, refresh in background if stale ─
  const cached = cacheGet<T>(cacheKey);
  const shouldRefresh = cacheShouldRefresh(cacheKey);

  if (cached && !shouldRefresh) {
    return cached; // fresh enough, return instantly
  }

  if (cached && shouldRefresh) {
    // Return the stale entry now, refresh in background
    refreshCache(url, cacheKey, init);
    return cached;
  }

  // Nothing cached — fetch normally (and cache the result)
  return doFetchAndCache<T>(url, cacheKey, init);
}

// ── Internal helpers ─────────────────────────────────────────────────

async function doFetchAndCache<T>(url: string, cacheKey: string, init?: RequestInit): Promise<T> {
  try {
    const merged: RequestInit = {
      headers: { "Content-Type": "application/json" },
      ...init,
    };
    const res = await fetch(url, merged);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Request failed (${res.status})`);
    }
    const data = (res.status === 204 ? undefined : await res.json()) as T;
    cacheSet(cacheKey, data);
    return data;
  } catch (err) {
    // If a previous cache entry exists (stale), serve it as fallback
    const fallback = cacheGet<T>(cacheKey);
    if (fallback !== null) return fallback;
    throw err;
  }
}

function refreshCache(url: string, cacheKey: string, init?: RequestInit): void {
  // Fire-and-forget: don't block the caller
  doFetchAndCache(url, cacheKey, init).catch((err) => {
    console.warn("[apiFetch] Background refresh failed:", url, err);
  });
}

/**
 * Force-clear the cache for a prefix (useful after external mutations).
 */
export { cacheInvalidate as invalidateCache, cacheClear } from "./cache";
