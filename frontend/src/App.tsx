import { API_BASE, api } from "./api";
import { EventDetail } from "./components/EventDetail";
import { Events } from "./components/Events";
import { Overview } from "./components/Overview";
import { useRoute } from "./router";
import { useApi } from "./useApi";

export function App() {
  const route = useRoute();
  const health = useApi((s) => api.health(s), "health", 15000);

  return (
    <div className="app">
      <header>
        <h1>
          <a href="#/">Soroban Event Indexer</a>
        </h1>
        <nav>
          <a href="#/" aria-current={route.name === "overview" ? "page" : undefined}>
            Overview
          </a>
          <a
            href="#/events"
            aria-current={route.name === "events" || route.name === "event" ? "page" : undefined}
          >
            Events
          </a>
        </nav>
        <span
          className={`status ${health.error ? "down" : health.data ? "up" : ""}`}
          title={`API: ${API_BASE}`}
        >
          {health.error ? "API down" : health.data ? `API v${health.data.version}` : "…"}
        </span>
      </header>
      <main>
        {route.name === "overview" && <Overview />}
        {route.name === "events" && <Events params={route.params} />}
        {route.name === "event" && <EventDetail id={route.id} />}
        {route.name === "not_found" && (
          <p>
            Page <code>{route.path}</code> not found. <a href="#/">Go home</a>.
          </p>
        )}
      </main>
    </div>
  );
}
