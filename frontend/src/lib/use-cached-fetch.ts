"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------------ */
/*  In-memory cache (shared across all hook instances)                 */
/* ------------------------------------------------------------------ */

interface CacheEntry<T> {
  data: T;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry<unknown>>();

function cacheKey(url: string, token: string | null): string {
  return `${url}::${token ?? "anon"}`;
}

/* ------------------------------------------------------------------ */
/*  useCachedFetch hook                                                */
/* ------------------------------------------------------------------ */

export interface UseCachedFetchOptions {
  /** Time-to-live in milliseconds. Default 60 000 (60 s). */
  ttl?: number;
  /** Skip the fetch entirely (e.g. when a dependency is not ready). */
  skip?: boolean;
}

export interface UseCachedFetchResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Force a fresh fetch, ignoring the cache. */
  refetch: () => void;
}

/**
 * Custom React hook that wraps a fetch-like async function with a
 * simple in-memory cache keyed by URL + token, with configurable TTL.
 *
 * @param fetcher  Async function that returns the data (called with no args).
 * @param cacheUrl Stable string used as the cache key (typically the endpoint URL).
 * @param token    Current auth token — included in the cache key so data is
 *                 never shared across users.
 * @param options  Optional `{ ttl, skip }`.
 */
export function useCachedFetch<T>(
  fetcher: () => Promise<T>,
  cacheUrl: string,
  token: string | null,
  options: UseCachedFetchOptions = {},
): UseCachedFetchResult<T> {
  const { ttl = 60_000, skip = false } = options;

  const [data, setData] = useState<T | null>(() => {
    if (skip || !token) return null;
    const key = cacheKey(cacheUrl, token);
    const entry = cache.get(key) as CacheEntry<T> | undefined;
    if (entry && entry.expiresAt > Date.now()) return entry.data;
    return null;
  });
  const [loading, setLoading] = useState(!skip && data === null);
  const [error, setError] = useState<string | null>(null);

  // Monotonically increasing counter to discard stale responses.
  const seqRef = useRef(0);

  const doFetch = useCallback(
    (ignoreCache: boolean) => {
      if (skip || !token) return;
      const key = cacheKey(cacheUrl, token);

      if (!ignoreCache) {
        const entry = cache.get(key) as CacheEntry<T> | undefined;
        if (entry && entry.expiresAt > Date.now()) {
          setData(entry.data);
          setLoading(false);
          setError(null);
          return;
        }
      }

      const seq = ++seqRef.current;
      setLoading(true);
      setError(null);

      fetcher()
        .then((result) => {
          if (seq !== seqRef.current) return; // stale
          cache.set(key, { data: result, expiresAt: Date.now() + ttl });
          setData(result);
        })
        .catch((err) => {
          if (seq !== seqRef.current) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (seq !== seqRef.current) return;
          setLoading(false);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cacheUrl, token, skip, ttl],
  );

  useEffect(() => {
    doFetch(false);
  }, [doFetch]);

  const refetch = useCallback(() => doFetch(true), [doFetch]);

  return { data, loading, error, refetch };
}

/**
 * Invalidate a specific cache entry.
 */
export function invalidateCache(url: string, token: string | null): void {
  cache.delete(cacheKey(url, token));
}

/**
 * Clear the entire cache.
 */
export function clearCache(): void {
  cache.clear();
}
