// Typed client for the indexer REST API. Shapes mirror backend/soroban_indexer/api.py.

export type EventType = "contract" | "system" | "diagnostic";

export interface IndexedEvent {
  id: string;
  ledger: number;
  ledger_closed_at: string | null;
  contract_id: string | null;
  type: EventType | string;
  tx_hash: string | null;
  in_successful_contract_call: boolean | null;
  topics: unknown[];
  value: unknown;
  topics_xdr: string[];
  value_xdr: string;
  decode_ok: boolean;
}

export interface EventsPage {
  items: IndexedEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface Contract {
  contract_id: string;
  event_count: number;
  first_ledger: number;
  last_ledger: number;
}

export interface Stats {
  total_events: number;
  contracts: number;
  min_ledger: number | null;
  max_ledger: number | null;
  undecodable_events: number;
  events_by_type: Record<string, number>;
  indexer: {
    cursor: string | null;
    start_ledger: number | null;
    last_seen_ledger: number | null;
    last_poll_at: string | null;
  };
}

export interface EventFilters {
  contract_id?: string;
  type?: EventType | "";
  topic0?: string;
  from_ledger?: number | "";
  to_ledger?: number | "";
  limit?: number;
  offset?: number;
}

/** Error raised for non-2xx responses, carrying the API's `{error: {code, message}}`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const API_BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/+$/, "");

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, { signal, headers: { Accept: "application/json" } });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network_error", `Cannot reach the API at ${API_BASE}`);
  }
  if (!resp.ok) {
    let code = "error";
    let message = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { error?: { code?: string; message?: string } };
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // Non-JSON error body (e.g. a proxy page); keep the generic message.
    }
    throw new ApiError(resp.status, code, message);
  }
  return (await resp.json()) as T;
}

/** Build the query string for GET /events, dropping empty filters. */
export function eventsQuery(filters: EventFilters): string {
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(filters)) {
    if (raw === undefined || raw === null || raw === "") continue;
    const value = typeof raw === "string" ? raw.trim() : String(raw);
    if (value !== "") params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  health: (signal?: AbortSignal) =>
    getJson<{ status: string; database: string; version: string }>("/health", signal),
  stats: (signal?: AbortSignal) => getJson<Stats>("/stats", signal),
  contracts: (signal?: AbortSignal) => getJson<{ items: Contract[] }>("/contracts", signal),
  events: (filters: EventFilters, signal?: AbortSignal) =>
    getJson<EventsPage>(`/events${eventsQuery(filters)}`, signal),
  event: (id: string, signal?: AbortSignal) =>
    getJson<IndexedEvent>(`/events/${encodeURIComponent(id)}`, signal),
};
