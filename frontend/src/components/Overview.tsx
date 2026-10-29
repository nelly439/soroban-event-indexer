import { api } from "../api";
import { formatNumber, timeAgo } from "../format";
import { eventsHref } from "../router";
import { useApi } from "../useApi";
import { Card, ErrorBox, Loading, Mono } from "./Common";

const REFRESH_MS = 5000;

export function Overview() {
  const stats = useApi((s) => api.stats(s), "stats", REFRESH_MS);
  const contracts = useApi((s) => api.contracts(s), "contracts", REFRESH_MS);

  return (
    <section>
      <h2>Overview</h2>
      {stats.error && <ErrorBox error={stats.error} onRetry={stats.reload} />}
      {!stats.data && !stats.error && <Loading />}
      {stats.data && (
        <>
          <div className="cards">
            <Card label="Events" value={formatNumber(stats.data.total_events)} />
            <Card label="Contracts" value={formatNumber(stats.data.contracts)} />
            <Card
              label="Ledger range"
              value={
                stats.data.min_ledger === null
                  ? "—"
                  : `${formatNumber(stats.data.min_ledger)} – ${formatNumber(stats.data.max_ledger)}`
              }
            />
            <Card
              label="Undecodable"
              value={formatNumber(stats.data.undecodable_events)}
              hint="events whose XDR failed to decode"
            />
          </div>
          <h3>Indexer</h3>
          <dl className="kv">
            <dt>Last poll</dt>
            <dd>{timeAgo(stats.data.indexer.last_poll_at)}</dd>
            <dt>Latest ledger (RPC)</dt>
            <dd>{formatNumber(stats.data.indexer.last_seen_ledger)}</dd>
            <dt>Start ledger</dt>
            <dd>{formatNumber(stats.data.indexer.start_ledger)}</dd>
            <dt>Cursor</dt>
            <dd>
              <Mono>{stats.data.indexer.cursor ?? "—"}</Mono>
            </dd>
            <dt>By type</dt>
            <dd>
              {Object.entries(stats.data.events_by_type).length === 0
                ? "—"
                : Object.entries(stats.data.events_by_type)
                    .map(([t, n]) => `${t}: ${formatNumber(n)}`)
                    .join(" · ")}
            </dd>
          </dl>
        </>
      )}

      <h3>Contracts</h3>
      {contracts.error && <ErrorBox error={contracts.error} onRetry={contracts.reload} />}
      {contracts.data && contracts.data.items.length === 0 && (
        <p className="muted">No events indexed yet. The indexer may still be catching up.</p>
      )}
      {contracts.data && contracts.data.items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Contract</th>
              <th className="num">Events</th>
              <th className="num">First ledger</th>
              <th className="num">Last ledger</th>
            </tr>
          </thead>
          <tbody>
            {contracts.data.items.map((c) => (
              <tr key={c.contract_id}>
                <td>
                  <a href={eventsHref({ contract_id: c.contract_id })}>
                    <Mono>{c.contract_id}</Mono>
                  </a>
                </td>
                <td className="num">{formatNumber(c.event_count)}</td>
                <td className="num">{formatNumber(c.first_ledger)}</td>
                <td className="num">{formatNumber(c.last_ledger)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
