import { api } from "../api";
import { formatNumber } from "../format";
import { eventsHref } from "../router";
import { useApi } from "../useApi";
import { ErrorBox, Loading, Mono } from "./Common";

export function EventDetail({ id }: { id: string }) {
  const ev = useApi((s) => api.event(id, s), id);

  return (
    <section>
      <p>
        <a href="#/events">← All events</a>
      </p>
      <h2>Event</h2>
      {ev.error && <ErrorBox error={ev.error} />}
      {!ev.data && !ev.error && <Loading />}
      {ev.data && (
        <>
          {!ev.data.decode_ok && (
            <div className="warning">
              Part of this event could not be decoded; the raw XDR is shown below.
            </div>
          )}
          <dl className="kv">
            <dt>ID</dt>
            <dd>
              <Mono>{ev.data.id}</Mono>
            </dd>
            <dt>Ledger</dt>
            <dd>
              {formatNumber(ev.data.ledger)}
              {ev.data.ledger_closed_at && ` · ${ev.data.ledger_closed_at}`}
            </dd>
            <dt>Contract</dt>
            <dd>
              {ev.data.contract_id ? (
                <a href={eventsHref({ contract_id: ev.data.contract_id })}>
                  <Mono>{ev.data.contract_id}</Mono>
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>Type</dt>
            <dd>{ev.data.type}</dd>
            <dt>Transaction</dt>
            <dd>
              <Mono>{ev.data.tx_hash ?? "—"}</Mono>
            </dd>
            <dt>Successful call</dt>
            <dd>
              {ev.data.in_successful_contract_call === null
                ? "—"
                : String(ev.data.in_successful_contract_call)}
            </dd>
          </dl>
          <h3>Topics</h3>
          <pre>{JSON.stringify(ev.data.topics, null, 2)}</pre>
          <h3>Value</h3>
          <pre>{JSON.stringify(ev.data.value, null, 2)}</pre>
          <details>
            <summary>Raw XDR</summary>
            <pre>{JSON.stringify({ topics: ev.data.topics_xdr, value: ev.data.value_xdr }, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
