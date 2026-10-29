#!/usr/bin/env bash
# Run the whole stack locally without Docker against the fake RPC:
# fake RPC (:8900) -> indexer -> SQLite -> API (:8000) -> dashboard (:5173).
# Logs and the database go to .demo-work/. Ctrl-C stops everything.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.demo-work"
PY="$ROOT/backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "backend venv missing: run 'make install' first" >&2; exit 1; }
[[ -d "$ROOT/frontend/node_modules" ]] || { echo "frontend deps missing: run 'make install' first" >&2; exit 1; }

mkdir -p "$WORK"
rm -f "$WORK"/demo.db*

export DATABASE_URL="sqlite:///$WORK/demo.db"
export RPC_URL="http://127.0.0.1:8900"
export START_LEDGER=1000
export POLL_INTERVAL_SECONDS=2
export API_HOST=127.0.0.1

pids=()
cleanup() {
  trap - INT TERM EXIT
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"$PY" "$ROOT/tests-e2e/fake_rpc.py" --port 8900 --ledgers 60 --tick 5 >"$WORK/fake-rpc.log" 2>&1 &
pids+=($!)
sleep 1
"$PY" -m soroban_indexer index >"$WORK/indexer.log" 2>&1 &
pids+=($!)
"$PY" -m soroban_indexer serve >"$WORK/api.log" 2>&1 &
pids+=($!)
(cd "$ROOT/frontend" && npx vite --port 5173 --strictPort >"$WORK/frontend.log" 2>&1) &
pids+=($!)

echo "Dashboard: http://localhost:5173"
echo "API:       http://localhost:8000/docs"
echo "Logs:      $WORK/*.log   (Ctrl-C to stop)"
wait
