"use client";

import { useEffect, useRef } from "react";

/**
 * Poll a data-loading callback at a given interval.
 *
 * The callback is called immediately on mount (mimicking the existing
 * useEffect(loadData, [loadData]) pattern), and again every `intervalMs`
 * milliseconds while the component is mounted.
 *
 * If the callback is a `useCallback` with stable deps, it integrates
 * cleanly with React's dependency tracking.
 *
 * @param cb     Async function that fetches/refreshes data.
 * @param intervalMs  Polling interval in milliseconds. Pass `null` to disable.
 */
export function usePolling(cb: () => Promise<void>, intervalMs: number | null): void {
  const savedCb = useRef(cb);

  // Keep the ref up to date so the interval always calls the latest callback
  useEffect(() => {
    savedCb.current = cb;
  }, [cb]);

  useEffect(() => {
    if (intervalMs === null || intervalMs <= 0) return;

    const id = setInterval(() => {
      savedCb.current().catch(() => {
        // Errors are handled inside the callback (setError, etc.)
      });
    }, intervalMs);

    return () => clearInterval(id);
  }, [intervalMs]);
}
