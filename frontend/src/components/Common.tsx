import type { ReactNode } from "react";

import { ApiError } from "../api";

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const title =
    error instanceof ApiError && error.code === "network_error" ? "API unreachable" : "Error";
  return (
    <div className="error" role="alert">
      <strong>{title}:</strong> {error.message}
      {onRetry && (
        <button type="button" className="link" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

export function Card({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      {hint && <div className="card-hint">{hint}</div>}
    </div>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <code className="mono" title={title}>
      {children}
    </code>
  );
}
