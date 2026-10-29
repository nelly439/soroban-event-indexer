import pytest

from soroban_indexer.config import ENV_VARS, ConfigError, load_config, parse_contract_ids

from .helpers import OTHER, TOKEN


def test_defaults():
    cfg = load_config({})
    assert cfg.database_url == "sqlite:///./soroban_events.db"
    assert cfg.rpc_url == "https://soroban-testnet.stellar.org"
    assert cfg.start_ledger is None
    assert cfg.contract_ids == ()
    assert cfg.poll_interval_seconds == 5.0
    assert cfg.page_limit == 100
    assert cfg.rpc_timeout_seconds == 30.0
    assert cfg.backoff_initial_seconds == 1.0
    assert cfg.backoff_max_seconds == 60.0
    assert cfg.api_host == "0.0.0.0"
    assert cfg.api_port == 8000
    assert cfg.cors_origins == ("*",)
    assert cfg.log_level == "INFO"


def test_every_env_var_has_a_default_entry():
    # The docs checker relies on ENV_VARS being complete.
    assert set(ENV_VARS) == {
        "DATABASE_URL",
        "RPC_URL",
        "START_LEDGER",
        "CONTRACT_IDS",
        "POLL_INTERVAL_SECONDS",
        "PAGE_LIMIT",
        "RPC_TIMEOUT_SECONDS",
        "BACKOFF_INITIAL_SECONDS",
        "BACKOFF_MAX_SECONDS",
        "API_HOST",
        "API_PORT",
        "CORS_ORIGINS",
        "LOG_LEVEL",
    }


def test_full_override():
    cfg = load_config(
        {
            "DATABASE_URL": "postgresql+psycopg://u:p@db/x",
            "RPC_URL": "http://rpc:8000",
            "START_LEDGER": " 123 ",
            "CONTRACT_IDS": f"{TOKEN}, {OTHER},{TOKEN},",
            "POLL_INTERVAL_SECONDS": "0.5",
            "PAGE_LIMIT": "200",
            "RPC_TIMEOUT_SECONDS": "3",
            "BACKOFF_INITIAL_SECONDS": "2",
            "BACKOFF_MAX_SECONDS": "10",
            "API_HOST": "127.0.0.1",
            "API_PORT": "9000",
            "CORS_ORIGINS": "http://a.test, http://b.test",
            "LOG_LEVEL": "debug",
        }
    )
    assert cfg.start_ledger == 123
    assert cfg.contract_ids == (TOKEN, OTHER)
    assert cfg.poll_interval_seconds == 0.5
    assert cfg.page_limit == 200
    assert cfg.api_port == 9000
    assert cfg.cors_origins == ("http://a.test", "http://b.test")
    assert cfg.log_level == "DEBUG"


def test_blank_values_fall_back_to_defaults():
    cfg = load_config({"START_LEDGER": "  ", "PAGE_LIMIT": "", "CORS_ORIGINS": " , "})
    assert cfg.start_ledger is None
    assert cfg.page_limit == 100
    assert cfg.cors_origins == ("*",)


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"START_LEDGER": "abc"}, "START_LEDGER must be an integer"),
        ({"START_LEDGER": "0"}, "START_LEDGER must be >= 1"),
        ({"PAGE_LIMIT": "0"}, "PAGE_LIMIT must be >= 1"),
        ({"PAGE_LIMIT": "10001"}, "<= 10000"),
        ({"API_PORT": "70000"}, "API_PORT"),
        ({"POLL_INTERVAL_SECONDS": "fast"}, "must be a number"),
        ({"POLL_INTERVAL_SECONDS": "-1"}, ">= 0.0"),
        ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
        ({"BACKOFF_INITIAL_SECONDS": "10", "BACKOFF_MAX_SECONDS": "5"}, "BACKOFF_MAX_SECONDS"),
        ({"CONTRACT_IDS": "GABC"}, "not a contract address"),
    ],
)
def test_invalid_values(env, match):
    with pytest.raises(ConfigError, match=match):
        load_config(env)


def test_too_many_contract_ids():
    from stellar_sdk import StrKey

    ids = ",".join(StrKey.encode_contract(bytes([i]) * 32) for i in range(26))
    with pytest.raises(ConfigError, match="at most 25"):
        parse_contract_ids(ids)


def test_reads_os_environ(monkeypatch):
    monkeypatch.setenv("PAGE_LIMIT", "7")
    assert load_config().page_limit == 7
