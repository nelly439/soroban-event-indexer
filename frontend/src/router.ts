// Minimal hash router: #/, #/events?contract_id=..., #/events/<id>.
import { useEffect, useState } from "react";

export type Route =
  | { name: "overview" }
  | { name: "events"; params: URLSearchParams }
  | { name: "event"; id: string }
  | { name: "not_found"; path: string };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?", 2);
  if (path === "/" || path === "") return { name: "overview" };
  if (path === "/events") return { name: "events", params: new URLSearchParams(query) };
  const m = /^\/events\/([^/]+)$/.exec(path);
  if (m) return { name: "event", id: decodeURIComponent(m[1]) };
  return { name: "not_found", path };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function eventsHref(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `#/events?${s}` : "#/events";
}
