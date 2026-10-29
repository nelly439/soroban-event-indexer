// Small display helpers shared by the views.

/** Shorten a long strkey / hash: CDLZ…CYSC. */
export function shorten(value: string | null | undefined, keep = 4): string {
  if (!value) return "—";
  return value.length <= keep * 2 + 1 ? value : `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

/** Compact one-line rendering of a decoded topic or value. */
export function preview(value: unknown, max = 80): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "—";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function formatNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("en-US");
}

/** "12s ago", "3m ago", ... relative to `now`; falls back to the raw string. */
export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const s = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
