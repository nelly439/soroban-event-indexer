import { ApiError, api, eventsQuery } from "../api";
import { mockFetch, page } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("eventsQuery", () => {
  it("drops empty values and trims strings", () => {
    expect(
      eventsQuery({ contract_id: "  C1 ", type: "", topic0: undefined, from_ledger: "", offset: 0 }),
    ).toBe("?contract_id=C1&offset=0");
  });

  it("returns an empty string with no filters", () => {
    expect(eventsQuery({})).toBe("");
  });

  it("encodes special characters", () => {
    expect(eventsQuery({ topic0: '["a",1]' })).toBe("?topic0=%5B%22a%22%2C1%5D");
  });
});

describe("api client", () => {
  it("fetches events with filters under /api", async () => {
    const calls = mockFetch({ "/events": page });
    const res = await api.events({ type: "contract", limit: 10 });
    expect(res.total).toBe(1);
    expect(calls[0].pathname).toBe("/api/events");
    expect(calls[0].searchParams.get("type")).toBe("contract");
    expect(calls[0].searchParams.get("limit")).toBe("10");
  });

  it("surfaces the API error envelope", async () => {
    mockFetch({});
    await expect(api.event("missing")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "nope",
    });
  });

  it("handles non-JSON error bodies", async () => {
    mockFetch({ "/stats": () => new Response("<html>bad gateway</html>", { status: 502 }) });
    await expect(api.stats()).rejects.toMatchObject({ status: 502, message: "HTTP 502" });
  });

  it("maps network failures to a network_error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await api.health().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("network_error");
  });
});
