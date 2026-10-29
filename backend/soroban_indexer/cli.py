"""Command-line entry point: ``soroban-indexer <command>``.

Commands:

* ``index``         run the polling indexer (``--once`` for a single poll)
* ``serve``         run the REST API with uvicorn
* ``init-db``       create tables and exit
* ``reset-cursor``  forget the stored cursor / start ledger
* ``status``        print database stats and indexer progress as JSON
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Sequence
from types import FrameType

from sqlalchemy import delete

from . import __version__, db
from .config import Config, ConfigError, load_config
from .indexer import Indexer
from .rpc import RpcClient, RpcError

log = logging.getLogger("soroban_indexer")


def setup_logging(level: str) -> None:
    """Configure root logging with a compact, greppable format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_index(config: Config, once: bool) -> int:
    """Run the indexer until SIGINT/SIGTERM (or a single poll with ``once``)."""
    engine = db.make_engine(config.database_url)
    db.init_db(engine)
    stop = threading.Event()

    def _handle(signum: int, _frame: FrameType | None) -> None:
        log.info("received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    with RpcClient(config.rpc_url, timeout=config.rpc_timeout_seconds) as rpc:
        indexer = Indexer(config, rpc, db.make_session_factory(engine))
        if once:
            try:
                result = indexer.poll_once()
            except RpcError as err:
                log.error("poll failed: %s", err)
                return 1
            print(
                json.dumps(
                    {
                        "new_events": result.new_events,
                        "pages": result.pages,
                        "cursor": result.cursor,
                        "latest_ledger": result.latest_ledger,
                    }
                )
            )
            return 0
        indexer.run_forever(stop)
    return 0


def cmd_serve(config: Config) -> int:
    """Serve the API on ``API_HOST:API_PORT``."""
    import uvicorn

    from .api import create_app

    uvicorn.run(
        create_app(config),
        host=config.api_host,
        port=config.api_port,
        log_level=config.log_level.lower(),
    )
    return 0


def cmd_init_db(config: Config) -> int:
    """Create database tables."""
    db.init_db(db.make_engine(config.database_url))
    print("database initialised")
    return 0


def cmd_reset_cursor(config: Config) -> int:
    """Delete the stored cursor and start ledger; indexed events are kept."""
    engine = db.make_engine(config.database_url)
    db.init_db(engine)
    with db.make_session_factory(engine)() as session:
        session.execute(
            delete(db.IndexerState).where(
                db.IndexerState.key.in_([db.STATE_CURSOR, db.STATE_START_LEDGER])
            )
        )
        session.commit()
    print("cursor reset; the next run starts at START_LEDGER or the latest ledger")
    return 0


def cmd_status(config: Config) -> int:
    """Print stats and indexer state as JSON."""
    engine = db.make_engine(config.database_url)
    db.init_db(engine)
    with db.make_session_factory(engine)() as session:
        out = {**db.event_stats(session), "state": db.get_all_state(session)}
    print(json.dumps(out, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="soroban-indexer",
        description="Index Soroban contract events from Stellar RPC and serve them over HTTP. "
        "Configuration comes from environment variables (see docs/configuration.md).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p_index = sub.add_parser("index", help="run the polling indexer")
    p_index.add_argument("--once", action="store_true", help="poll once, print a summary, exit")
    sub.add_parser("serve", help="run the REST API")
    sub.add_parser("init-db", help="create database tables")
    sub.add_parser("reset-cursor", help="forget the stored cursor and start ledger")
    sub.add_parser("status", help="print stats and indexer state as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ConfigError as err:
        print(f"configuration error: {err}", file=sys.stderr)
        return 2
    setup_logging(config.log_level)
    if args.command == "index":
        return cmd_index(config, once=args.once)
    if args.command == "serve":
        return cmd_serve(config)
    if args.command == "init-db":
        return cmd_init_db(config)
    if args.command == "reset-cursor":
        return cmd_reset_cursor(config)
    return cmd_status(config)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
