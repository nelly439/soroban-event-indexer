import { type FormEvent, useState } from "react";

import { api, type EventFilters, type EventType } from "../api";
import { formatNumber, preview, shorten } from "../format";
import { eventsHref } from "../router";
import { useApi } from "../useApi";
import { ErrorBox, Loading, Mono } from "./Common";

export const PAGE_SIZE = 50;
const FILTER_KEYS = ["contract_id", "type", "topic0", "from_ledger", "to_ledger"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type FormState = Record<FilterKey, string>;

function toInt(value: string | null): number | "" {
  if (!value) return "";
  const n = Number(value);
  return Number.isInteger(n) && n >= 0 ? n : "";
}

/** Read filters + offset from the hash query string. */
export function filtersFromParams(params: URLSearchParams): EventFilters {
  return {
    contract_id: params.get("contract_id") ?? "",
    type: (params.get("type") ?? "") as EventType | "",
    topic0: params.get("topic0") ?? "",
    from_ledger: toInt(params.get("from_ledger")),
    to_ledger: toInt(params.get("to_ledger")),
    limit: PAGE_SIZE,
    offset: toInt(params.get("offset")) || 0,
  };
}

function formFrom(f: EventFilters): FormState {
  return {
    contract_id: f.contract_id ?? "",
    type: f.type ?? "",
    topic0: f.topic0 ?? "",
    from_ledger: f.from_ledger === undefined ? "" : String(f.from_ledger),
    to_ledger: f.to_ledger === undefined ? "" : String(f.to_ledger),
  };
}

export function Events({ params }: { params: URLSearchParams }) {
  const filters = filtersFromParams(params);
  const key = JSON.stringify(filters);
  const page = useApi((s) => api.events(filters, s), key);
  const [form, setForm] = useState<FormState>(() => formFrom(filters));
  const [formKey, setFormKey] = useState(key);
  if (formKey !== key) {
    // Navigated (e.g. clicked a contract link): resync the form with the URL.
    setFormKey(key);
    setForm(formFrom(filters));
  }

  const navigate = (next: Partial<FormState> & { offset?: number }) => {
    window.location.hash = eventsHref({ ...form, ...next });
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    navigate({ offset: 0 });
  };
  const set = (k: FilterKey) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const offset = filters.offset ?? 0;
  const data = page.data;

  return (
    <section>
      <h2>Events</h2>
      <form className="filters" onSubmit={onSubmit} aria-label="Event filters">
        <label>
          Contract
          <input
            value={form.contract_id}
            onChange={set("contract_id")}
            placeholder="C…"
            maxLength={64}
          />
        </label>
        <label>
          Type
          <select value={form.type} onChange={set("type")}>
            <option value="">any</option>
            <option value="contract">contract</option>
            <option value="system">system</option>
            <option value="diagnostic">diagnostic</option>
          </select>
        </label>
        <label>
          Topic 0
          <input value={form.topic0} onChange={set("topic0")} placeholder="transfer" />
        </label>
        <label>
          From ledger
          <input type="number" min={0} value={form.from_ledger} onChange={set("from_ledger")} />
        </label>
        <label>
          To ledger
          <input type="number" min={0} value={form.to_ledger} onChange={set("to_ledger")} />
        </label>
        <div className="filter-actions">
          <button type="submit">Apply</button>
          <a className="button secondary" href="#/events">
            Clear
          </a>
        </div>
      </form>

      {page.error && <ErrorBox error={page.error} onRetry={page.reload} />}
      {!data && !page.error && <Loading />}
      {data && (
        <>
          <p className="muted">
            {data.total === 0
              ? "No events match these filters."
              : `Showing ${formatNumber(offset + 1)}–${formatNumber(
                  offset + data.items.length,
                )} of ${formatNumber(data.total)}`}
            {page.loading && " · refreshing…"}
          </p>
          {data.items.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th className="num">Ledger</th>
                  <th>Contract</th>
                  <th>Type</th>
                  <th>Topics</th>
                  <th>Value</th>
                  <th>Tx</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((ev) => (
                  <tr key={ev.id} className={ev.decode_ok ? undefined : "undecodable"}>
                    <td className="num">
                      <a href={`#/events/${encodeURIComponent(ev.id)}`}>{formatNumber(ev.ledger)}</a>
                    </td>
                    <td>
                      {ev.contract_id ? (
                        <a
                          href={eventsHref({ contract_id: ev.contract_id })}
                          title={ev.contract_id}
                        >
                          <Mono>{shorten(ev.contract_id)}</Mono>
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{ev.type}</td>
                    <td>
                      <Mono>{ev.topics.map((t) => preview(t, 24)).join(", ")}</Mono>
                    </td>
                    <td>
                      <Mono>{preview(ev.value, 40)}</Mono>
                    </td>
                    <td>
                      <Mono title={ev.tx_hash ?? undefined}>{shorten(ev.tx_hash, 6)}</Mono>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <nav className="pager" aria-label="Pagination">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => navigate({ ...formFrom(filters), offset: Math.max(0, offset - PAGE_SIZE) })}
            >
              ← Newer
            </button>
            <button
              type="button"
              disabled={offset + data.items.length >= data.total}
              onClick={() => navigate({ ...formFrom(filters), offset: offset + PAGE_SIZE })}
            >
              Older →
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
