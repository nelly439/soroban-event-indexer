import { useCallback, useEffect, useState } from "react";

export interface ApiState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Run `fetcher` whenever `key` changes (and every `refreshMs`, if set),
 * aborting stale requests. Keeps the previous data while reloading.
 */
export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  key: string,
  refreshMs?: number,
): ApiState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    fetcher(ctrl.signal)
      .then((d) => {
        setData(d);
        setError(undefined);
      })
      .catch((e: Error) => {
        if (e.name !== "AbortError") setError(e);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
    // `key` captures everything the fetcher depends on.
  }, [key, tick]);

  useEffect(() => {
    if (!refreshMs) return;
    const id = window.setInterval(reload, refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs, reload]);

  return { data, error, loading, reload };
}
