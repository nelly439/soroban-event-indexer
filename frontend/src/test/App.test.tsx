import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../App";
import { TOKEN, contracts, event, mockFetch, page, stats } from "./fixtures";

const health = { status: "ok", database: "ok", version: "0.1.0" };

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows stats and contracts on the overview", async () => {
    mockFetch({ "/health": health, "/stats": stats, "/contracts": contracts });
    render(<App />);
    expect(await screen.findByText(TOKEN)).toBeInTheDocument();
    const card = screen.getAllByText("Events").find((el) => el.closest(".card"));
    expect(card?.closest(".card")).toHaveTextContent("7");
    expect(await screen.findByText("API v0.1.0")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is indexed", async () => {
    mockFetch({
      "/health": health,
      "/stats": { ...stats, total_events: 0, contracts: 0, min_ledger: null, max_ledger: null },
      "/contracts": { items: [] },
    });
    render(<App />);
    expect(await screen.findByText(/No events indexed yet/)).toBeInTheDocument();
  });

  it("reports an unreachable API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect((await screen.findAllByText(/API unreachable/)).length).toBeGreaterThan(0);
    expect(await screen.findByText("API down")).toBeInTheDocument();
  });

  it("lists events and applies filters through the URL", async () => {
    window.location.hash = "#/events";
    const calls = mockFetch({ "/health": health, "/events": page });
    render(<App />);
    expect(await screen.findByText("1,000")).toBeInTheDocument();
    expect(screen.getByText(/Showing 1–1 of 1/)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Topic 0"), "transfer");
    await user.selectOptions(screen.getByLabelText("Type"), "contract");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      const last = calls.filter((c) => c.pathname === "/api/events").at(-1)!;
      expect(last.searchParams.get("topic0")).toBe("transfer");
      expect(last.searchParams.get("type")).toBe("contract");
    });
    expect(window.location.hash).toContain("topic0=transfer");
  });

  it("disables paging past the end", async () => {
    window.location.hash = "#/events";
    mockFetch({ "/health": health, "/events": page });
    render(<App />);
    await screen.findByText("1,000");
    expect(screen.getByRole("button", { name: /Newer/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Older/ })).toBeDisabled();
  });

  it("shows a single event with decoded values", async () => {
    window.location.hash = `#/events/${event.id}`;
    mockFetch({ "/health": health, [`/events/${event.id}`]: event });
    render(<App />);
    expect(await screen.findByText(event.id)).toBeInTheDocument();
    expect(screen.getByText(/12345678901234567890/)).toBeInTheDocument();
  });

  it("shows the API error for a missing event", async () => {
    window.location.hash = "#/events/missing";
    mockFetch({ "/health": health });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("flags undecodable events", async () => {
    window.location.hash = `#/events/${event.id}`;
    mockFetch({ "/health": health, [`/events/${event.id}`]: { ...event, decode_ok: false } });
    render(<App />);
    expect(await screen.findByText(/could not be decoded/)).toBeInTheDocument();
  });
});
