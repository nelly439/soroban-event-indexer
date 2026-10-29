import json

import pytest

from soroban_indexer import cli, db

from .conftest import FakeRpc
from .helpers import rpc_event


@pytest.fixture
def env(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("START_LEDGER", "1")
    return url


class FakeRpcClient(FakeRpc):
    instances: list["FakeRpcClient"] = []

    def __init__(self, url, timeout=30.0):
        super().__init__([rpc_event(1), rpc_event(2)], latest=5)
        self.url = url
        FakeRpcClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_init_db(env, capsys):
    assert cli.main(["init-db"]) == 0
    assert "initialised" in capsys.readouterr().out


def test_index_once_then_status_then_reset(env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "RpcClient", FakeRpcClient)
    assert cli.main(["index", "--once"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["new_events"] == 2 and summary["latest_ledger"] == 5

    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["total_events"] == 2
    assert "cursor" in status["state"]

    assert cli.main(["reset-cursor"]) == 0
    capsys.readouterr()
    cli.main(["status"])
    status = json.loads(capsys.readouterr().out)
    assert "cursor" not in status["state"] and "start_ledger" not in status["state"]
    assert status["total_events"] == 2  # events are kept


def test_index_once_reports_rpc_failure(env, monkeypatch):
    class Failing(FakeRpcClient):
        def get_events(self, **kw):
            raise cli.RpcError("down")

    monkeypatch.setattr(cli, "RpcClient", Failing)
    assert cli.main(["index", "--once"]) == 1


def test_index_loop_stops(env, monkeypatch):
    monkeypatch.setattr(cli, "RpcClient", FakeRpcClient)
    calls = {}

    def fake_run_forever(self, stop=None, **kw):
        calls["stop"] = stop
        stop.set()

    monkeypatch.setattr(cli.Indexer, "run_forever", fake_run_forever)
    assert cli.main(["index"]) == 0
    assert calls["stop"].is_set()


def test_serve_invokes_uvicorn(env, monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert cli.main(["serve"]) == 0
    assert seen["port"] == 8000 and seen["host"] == "0.0.0.0"


def test_config_error_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("PAGE_LIMIT", "nope")
    assert cli.main(["status"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_signal_handler_sets_stop(env, monkeypatch):
    import signal

    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.setdefault(sig, h))
    monkeypatch.setattr(cli, "RpcClient", FakeRpcClient)

    def fake_run_forever(self, stop=None, **kw):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert stop.is_set()

    monkeypatch.setattr(cli.Indexer, "run_forever", fake_run_forever)
    assert cli.main(["index"]) == 0


def test_requires_command():
    with pytest.raises(SystemExit):
        cli.main([])


def test_db_chunked():
    assert list(db.chunked(range(5), 2)) == [[0, 1], [2, 3], [4]]
