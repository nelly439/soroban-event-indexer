import type { Contract, EventsPage, IndexedEvent, Stats } from "../api";

export const TOKEN = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC";

export const event: IndexedEvent = {
  id: "0004294967296004096-0000000000",
  ledger: 1000,
  ledger_closed_at: "2026-10-29T12:00:00Z",
  contract_id: TOKEN,
  type: "contract",
  tx_hash: "ab".repeat(32),
  in_successful_contract_call: true,
  topics: ["transfer", "GAAZI4TCR3TY5OJHCTJC2A4QSY6CJWJH5IAJTGKIN2ER7LBNVKOCCWN7"],
  value: "12345678901234567890",
  topics_xdr: ["AAAADwAAAAh0cmFuc2Zlcg=="],
  value_xdr: "AAAACgAAAAAAAAAAAAAAAAAAA+g=",
  decode_ok: true,
};

export const page: EventsPage = { items: [event], total: 1, limit: 50, offset: 0 };

export const contracts: { items: Contract[] } = {
  items: [{ contract_id: TOKEN, event_count: 7, first_ledger: 900, last_ledger: 1000 }],
};

export const stats: Stats = {
  total_events: 7,
  contracts: 1,
  min_ledger: 900,
  max_ledger: 1000,
  undecodable_events: 0,
  events_by_type: { contract: 7 },
  indexer: {
    cursor: "0004294967296004096-0000000000",
    start_ledger: 900,
    last_seen_ledger: 1005,
    last_poll_at: new Date().toISOString(),
  },
};

type Routes = Record<string, unknown | ((url: URL) => Response)>;

/** Install a fetch mock answering by pathname (after the /api prefix). */
export function mockFetch(routes: Routes) {
  const calls: URL[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    const path = url.pathname.replace(/^\/api/, "");
    const handler = routes[path];
    if (handler === undefined) {
      return new Response(JSON.stringify({ error: { code: "not_found", message: "nope" } }), {
        status: 404,
      });
    }
    if (typeof handler === "function") return (handler as (u: URL) => Response)(url);
    return new Response(JSON.stringify(handler), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}
