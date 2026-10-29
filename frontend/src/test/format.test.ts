import { formatNumber, preview, shorten, timeAgo } from "../format";
import { eventsHref, parseHash } from "../router";

describe("format", () => {
  it("shortens long values only", () => {
    expect(shorten("CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC")).toBe("CDLZ…CYSC");
    expect(shorten("short")).toBe("short");
    expect(shorten(null)).toBe("—");
  });

  it("previews values as compact JSON", () => {
    expect(preview("transfer")).toBe("transfer");
    expect(preview({ a: [1, 2] })).toBe('{"a":[1,2]}');
    expect(preview("x".repeat(100), 10)).toBe(`${"x".repeat(9)}…`);
    expect(preview(undefined)).toBe("—");
  });

  it("formats numbers", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
    expect(formatNumber(null)).toBe("—");
  });

  it("formats relative times", () => {
    const now = new Date("2026-10-29T12:00:00Z");
    expect(timeAgo("2026-10-29T11:59:30Z", now)).toBe("30s ago");
    expect(timeAgo("2026-10-29T11:30:00Z", now)).toBe("30m ago");
    expect(timeAgo("2026-10-29T09:00:00Z", now)).toBe("3h ago");
    expect(timeAgo("2026-10-27T12:00:00Z", now)).toBe("2d ago");
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("garbage", now)).toBe("garbage");
  });
});

describe("router", () => {
  it("parses routes", () => {
    expect(parseHash("")).toEqual({ name: "overview" });
    expect(parseHash("#/")).toEqual({ name: "overview" });
    const r = parseHash("#/events?type=contract");
    expect(r.name).toBe("events");
    expect(r.name === "events" && r.params.get("type")).toBe("contract");
    expect(parseHash("#/events/abc%2Fdef")).toEqual({ name: "event", id: "abc/def" });
    expect(parseHash("#/nope")).toEqual({ name: "not_found", path: "/nope" });
  });

  it("builds event list links without empty params", () => {
    expect(eventsHref({ contract_id: "C1", type: "", offset: 0 })).toBe(
      "#/events?contract_id=C1&offset=0",
    );
    expect(eventsHref({})).toBe("#/events");
  });
});
