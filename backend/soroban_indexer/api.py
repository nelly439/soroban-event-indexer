"""Read-only REST API over the indexed events (FastAPI).

NOTE: this module intentionally does *not* use ``from __future__ import
annotations``. FastAPI resolves the ``Annotated[..., Depends(...)]`` aliases
below at runtime; with postponed evaluation it would mistake the session
dependency for a query parameter.
"""

import logging
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, db
from .config import Config, load_config

log = logging.getLogger("soroban_indexer.api")

MAX_LIMIT = 500


# --- response models -----------------------------------------------------------


class ErrorBody(BaseModel):
    """Machine-readable error description."""

    code: str = Field(examples=["not_found"])
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Envelope used for every non-2xx response."""

    error: ErrorBody


class HealthOut(BaseModel):
    """Liveness / readiness result."""

    status: Literal["ok"]
    database: Literal["ok"]
    version: str


class EventOut(BaseModel):
    """A decoded contract event."""

    id: str = Field(description="RPC event id; unique and stable.")
    ledger: int
    ledger_closed_at: str | None
    contract_id: str | None
    type: str = Field(description="contract, system or diagnostic")
    tx_hash: str | None
    in_successful_contract_call: bool | None
    topics: list[Any] = Field(description="Decoded topics (see docs/decoding.md).")
    value: Any = Field(description="Decoded event value (see docs/decoding.md).")
    topics_xdr: list[str] = Field(description="Original base64 XDR of each topic.")
    value_xdr: str = Field(description="Original base64 XDR of the value.")
    decode_ok: bool = Field(description="False if any topic or the value failed to decode.")


class EventsPageOut(BaseModel):
    """A page of events, newest first."""

    items: list[EventOut]
    total: int = Field(description="Number of events matching the filters.")
    limit: int
    offset: int


class ContractOut(BaseModel):
    """Aggregate information about one contract."""

    contract_id: str
    event_count: int
    first_ledger: int
    last_ledger: int


class ContractsOut(BaseModel):
    """All contracts that have emitted at least one indexed event."""

    items: list[ContractOut]


class IndexerStatus(BaseModel):
    """Progress information written by the indexer process."""

    cursor: str | None
    start_ledger: int | None
    last_seen_ledger: int | None = Field(description="Latest ledger reported by RPC.")
    last_poll_at: str | None


class StatsOut(BaseModel):
    """Database-wide counters plus indexer progress."""

    total_events: int
    contracts: int
    min_ledger: int | None
    max_ledger: int | None
    undecodable_events: int
    events_by_type: dict[str, int]
    indexer: IndexerStatus


# --- errors --------------------------------------------------------------------

_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(Exception):
    """Raise from a handler to return the standard error envelope."""

    def __init__(self, status: int, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _error(status: int, code: str, message: str, details: Any | None = None) -> JSONResponse:
    """Build the standard ``{"error": {...}}`` response."""
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump())


def _install_error_handlers(app: FastAPI) -> None:
    """Make every error (ours, routing, validation, crashes) use one shape."""

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "Invalid request parameters", details)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _error(500, "internal_error", "Internal server error")


# --- app factory ---------------------------------------------------------------

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid parameters"},
}


def create_app(config: Config | None = None, engine: Engine | None = None) -> FastAPI:
    """Create the FastAPI application.

    ``engine`` may be injected (tests); otherwise one is built from
    ``config.database_url``. Tables are created if missing.
    """
    config = config or load_config()
    engine = engine or db.make_engine(config.database_url)
    db.init_db(engine)
    session_factory = db.make_session_factory(engine)

    app = FastAPI(
        title="Soroban Event Indexer API",
        version=__version__,
        description=(
            "Read-only API over Soroban contract events indexed from Stellar RPC. "
            "Integers larger than 2^53-1 are returned as decimal strings."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    _install_error_handlers(app)

    def get_session() -> Iterator[Session]:
        """Yield a database session for one request."""
        with session_factory() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @app.get("/health", response_model=HealthOut, responses={503: {"model": ErrorResponse}})
    def health(session: SessionDep) -> HealthOut:
        """Return ``ok`` if the API is up and the database answers a query."""
        try:
            session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            raise ApiError(503, "unavailable", f"database unavailable: {exc}") from exc
        return HealthOut(status="ok", database="ok", version=__version__)

    @app.get(
        "/events",
        response_model=EventsPageOut,
        responses={400: {"model": ErrorResponse}, **_ERROR_RESPONSES},
    )
    def list_events(
        session: SessionDep,
        contract_id: Annotated[
            str | None, Query(max_length=64, description="Exact contract strkey (C...).")
        ] = None,
        type: Annotated[  # noqa: A002 - public query parameter name
            Literal["contract", "system", "diagnostic"] | None,
            Query(description="Event type."),
        ] = None,
        topic0: Annotated[
            str | None,
            Query(
                description=(
                    "Exact match on the first decoded topic. Strings/symbols/addresses match "
                    "verbatim (e.g. `transfer`); other values match their compact JSON."
                )
            ),
        ] = None,
        from_ledger: Annotated[int | None, Query(ge=0, description="Inclusive.")] = None,
        to_ledger: Annotated[int | None, Query(ge=0, description="Inclusive.")] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> EventsPageOut:
        """List events, newest first (ledger desc, then id desc)."""
        if from_ledger is not None and to_ledger is not None and from_ledger > to_ledger:
            raise ApiError(
                400, "bad_request", "from_ledger must be less than or equal to to_ledger"
            )
        rows, total = db.query_events(
            session,
            db.EventQuery(
                contract_id=contract_id,
                type=type,
                topic0=topic0,
                from_ledger=from_ledger,
                to_ledger=to_ledger,
                limit=limit,
                offset=offset,
            ),
        )
        return EventsPageOut(
            items=[EventOut(**r.to_dict()) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/events/{event_id}",
        response_model=EventOut,
        responses={404: {"model": ErrorResponse}},
    )
    def get_event(
        session: SessionDep,
        event_id: Annotated[str, Path(max_length=64, description="Event id.")],
    ) -> EventOut:
        """Return one event by id."""
        row = session.get(db.Event, event_id)
        if row is None:
            raise ApiError(404, "not_found", f"event {event_id!r} not found")
        return EventOut(**row.to_dict())

    @app.get("/contracts", response_model=ContractsOut)
    def contracts(session: SessionDep) -> ContractsOut:
        """List contracts with their event counts, busiest first."""
        return ContractsOut(items=[ContractOut(**c) for c in db.list_contracts(session)])

    @app.get("/stats", response_model=StatsOut)
    def stats(session: SessionDep) -> StatsOut:
        """Return global counters and the indexer's progress."""
        state = db.get_all_state(session)

        def _int(key: str) -> int | None:
            raw = state.get(key)
            return int(raw) if raw else None

        return StatsOut(
            **db.event_stats(session),
            indexer=IndexerStatus(
                cursor=state.get(db.STATE_CURSOR),
                start_ledger=_int(db.STATE_START_LEDGER),
                last_seen_ledger=_int(db.STATE_LAST_LEDGER),
                last_poll_at=state.get(db.STATE_LAST_POLL),
            ),
        )

    return app
