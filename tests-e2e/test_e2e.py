"""End-to-end tests: real `soroban-indexer` processes against the fake RPC server.

Run from the repository root with the backend virtualenv::

    backend/.venv/bin/pytest tests-e2e
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_rpc  # noqa: E402

PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(check: Callable[[], bool], timeout: float = 20, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


class Rpc:
    def __init__(self, url: str) -> None:
        self.url = url

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return httpx.post(self.url, json=body, timeout=5).json()["result"]

    def close_ledger(self) -> int:
        return int(self.call("_closeLedger")["sequence"])

    def all_events(self, contract_ids: list[str] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "startLedger": fake_rpc.FIRST_LEDGER,
            "pagination": {"limit": 10_000},
        }
        if contract_ids:
            params["filters"] = [{"contractIds": contract_ids}]
        return list(self.call("getEvents", params)["events"])


@pytest.fixture
def start_rpc(tmp_path: Path) -> Iterator[Callable[..., Rpc]]:
    procs: list[subprocess.Popen[bytes]] = []

    def _start(*args: str) -> Rpc:
        port = free_port()
        proc = subprocess.Popen(
            [PY, str(HERE / "fake_rpc.py"), "--port", str(port), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(proc)
        rpc = Rpc(f"http://127.0.0.1:{port}")
        wait_for(lambda: rpc.call("getHealth")["status"] == "healthy", what="fake RPC")
        return rpc

    yield _start
    for p in procs:
        p.terminate()
        p.wait(10)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}",
        "START_LEDGER": str(fake_rpc.FIRST_LEDGER),
        "PAGE_LIMIT": "7",
        "POLL_INTERVAL_SECONDS": "0.2",
        "BACKOFF_INITIAL_SECONDS": "0.1",
        "BACKOFF_MAX_SECONDS": "0.5",
        "LOG_LEVEL": "INFO",
    }


def cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "-m", "soroban_indexer", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def start_bg(tmp_path: Path) -> Iterator[Callable[..., subprocess.Popen[str]]]:
    procs: list[subprocess.Popen[str]] = []

    def _start(env: dict[str, str], *args: str) -> subprocess.Popen[str]:
        log = open(tmp_path / f"{args[0]}-{len(procs)}.log", "w")  # noqa: SIM115
        proc = subprocess.Popen(
            [PY, "-m", "soroban_indexer", *args],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(proc)
        return proc

    yield _start
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


def start_api(start_bg: Callable[..., subprocess.Popen[str]], env: dict[str, str]) -> str:
    port = free_port()
    start_bg({**env, "API_HOST": "127.0.0.1", "API_PORT": str(port)}, "serve")
    base = f"http://127.0.0.1:{port}"
    wait_for(lambda: httpx.get(f"{base}/health").status_code == 200, what="API")
    return base


# --- tests -----------------------------------------------------------------------


def test_backfill_is_complete_and_idempotent(start_rpc, env) -> None:
    rpc = start_rpc("--ledgers", "40")
    env["RPC_URL"] = rpc.url
    expected = rpc.all_events()

    first = cli(env, "index", "--once")
    assert first.returncode == 0, first.stderr
    summary = json.loads(first.stdout)
    assert summary["new_events"] == len(expected)
    assert summary["pages"] > 1  # PAGE_LIMIT=7 forces pagination

    again = cli(env, "index", "--once")
    assert json.loads(again.stdout)["new_events"] == 0

    rpc.close_ledger()
    rpc.close_ledger()
    after = cli(env, "index", "--once")
    assert json.loads(after.stdout)["new_events"] == len(rpc.all_events()) - len(expected)

    status = json.loads(cli(env, "status").stdout)
    assert status["total_events"] == len(rpc.all_events())


def test_api_serves_indexed_events(start_rpc, start_bg, env) -> None:
    rpc = start_rpc("--ledgers", "25")
    env["RPC_URL"] = rpc.url
    assert cli(env, "index", "--once").returncode == 0
    base = start_api(start_bg, env)
    expected = rpc.all_events()

    page = httpx.get(f"{base}/events", params={"limit": 500}).json()
    assert page["total"] == len(expected)
    assert {e["id"] for e in page["items"]} == {e["id"] for e in expected}
    ledgers = [e["ledger"] for e in page["items"]]
    assert ledgers == sorted(ledgers, reverse=True)

    mints = httpx.get(f"{base}/events", params={"topic0": "mint"}).json()
    for ev in mints["items"]:
        assert ev["topics"][0] == "mint"
        assert isinstance(ev["value"], str) and int(ev["value"]) > 2**53  # big int as string

    one = httpx.get(f"{base}/events/{expected[0]['id']}").json()
    assert one["value_xdr"] == expected[0]["value"]
    assert one["decode_ok"] is True

    missing = httpx.get(f"{base}/events/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    contracts = httpx.get(f"{base}/contracts").json()["items"]
    assert sum(c["event_count"] for c in contracts) == len(expected)

    stats = httpx.get(f"{base}/stats").json()
    assert stats["total_events"] == len(expected)
    assert stats["indexer"]["last_seen_ledger"] == fake_rpc.FIRST_LEDGER + 24


def test_continuous_indexing_and_graceful_shutdown(start_rpc, start_bg, env) -> None:
    rpc = start_rpc("--ledgers", "5")
    env["RPC_URL"] = rpc.url
    indexer = start_bg(env, "index")
    base = start_api(start_bg, env)

    def indexed() -> int:
        return int(httpx.get(f"{base}/stats").json()["total_events"])

    wait_for(lambda: indexed() == len(rpc.all_events()), what="initial backfill")
    for _ in range(5):
        rpc.close_ledger()
    wait_for(lambda: indexed() == len(rpc.all_events()), what="new ledgers")

    indexer.send_signal(signal.SIGTERM)
    assert indexer.wait(15) == 0


def test_recovers_from_rpc_outage(start_rpc, start_bg, env) -> None:
    rpc = start_rpc("--ledgers", "5")
    env["RPC_URL"] = rpc.url
    expected = len(rpc.all_events())
    rpc.call("_failNext", {"count": 4})
    start_bg(env, "index")
    base = start_api(start_bg, env)
    wait_for(
        lambda: httpx.get(f"{base}/stats").json()["total_events"] == expected,
        what="recovery after outage",
    )


def test_contract_filter(start_rpc, env) -> None:
    rpc = start_rpc("--ledgers", "30")
    env["RPC_URL"] = rpc.url
    env["CONTRACT_IDS"] = fake_rpc.DEX
    assert cli(env, "index", "--once").returncode == 0
    status = json.loads(cli(env, "status").stdout)
    assert status["total_events"] == len(rpc.all_events([fake_rpc.DEX]))
    assert status["contracts"] == 1


def test_retention_window_error_is_actionable(start_rpc, env) -> None:
    rpc = start_rpc("--ledgers", "30", "--oldest", "1010")
    env["RPC_URL"] = rpc.url
    result = cli(env, "index", "--once")
    assert result.returncode == 1
    assert "1010" in result.stderr
    assert "How to fix" in result.stderr


def test_bad_config_exits_with_code_2(env) -> None:
    env["PAGE_LIMIT"] = "zero"
    result = cli(env, "status")
    assert result.returncode == 2
    assert "PAGE_LIMIT" in result.stderr
