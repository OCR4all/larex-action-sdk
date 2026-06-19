from __future__ import annotations

# pyright: reportUnusedFunction=false
import logging
import os
from collections.abc import Awaitable, Callable, Iterable

from .client import ActionClient, ActionContext
from .exceptions import ActionCancelled, DispatchVerificationError
from .models import ActionDispatchPayload
from .nonce import NonceStore
from .verifier import DispatchVerifier

try:
    from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install larex-action-sdk[fastapi] to use larex_actions.fastapi") from exc

Handler = Callable[[ActionContext], Awaitable[None]]
ClientFactory = Callable[[ActionDispatchPayload], ActionClient]

logger = logging.getLogger(__name__)


def create_larex_action_app(
    *,
    processor_id: str,
    handler: Handler,
    dispatch_secret: str | None = None,
    dispatch_secret_env: str = "LAREX_DISPATCH_HMAC_SECRET",
    max_clock_skew_seconds: int = 300,
    nonce_store: NonceStore | None = None,
    app: FastAPI | None = None,
    client_factory: ClientFactory | None = None,
    enforce_url_security: bool = True,
    allowed_callback_origins: Iterable[str] | None = None,
    allowed_callback_origins_env: str = "LAREX_ALLOWED_CALLBACK_ORIGINS",
    allow_insecure_local_urls: bool = True,
    max_dispatch_body_bytes: int = 1_048_576,
    route_prefixes: Iterable[str] | None = None,
    route_prefixes_env: str = "LAREX_ACTION_ROUTE_PREFIXES",
) -> FastAPI:
    if max_dispatch_body_bytes <= 0:
        raise ValueError("max_dispatch_body_bytes must be positive")
    resolved_secret = dispatch_secret or os.getenv(dispatch_secret_env)
    if not resolved_secret:
        raise ValueError(f"Dispatch secret is not configured: {dispatch_secret_env}")

    verifier = DispatchVerifier(
        processor_id=processor_id,
        dispatch_secret=resolved_secret,
        nonce_store=nonce_store,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )
    resolved_allowed_origins = _resolve_allowed_origins(
        allowed_callback_origins,
        allowed_callback_origins_env,
    )
    resolved_route_prefixes = _resolve_route_prefixes(route_prefixes, route_prefixes_env)
    fastapi_app = app or FastAPI(title=f"LAREX Action Processor: {processor_id}")

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def dispatch(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
        body = await _read_limited_body(request, max_dispatch_body_bytes)
        raw_path = request.scope.get("raw_path")
        if isinstance(raw_path, bytes):
            path_and_query = raw_path.decode("ascii", errors="surrogateescape")
        else:
            path_and_query = request.url.path
        if request.url.query:
            path_and_query += f"?{request.url.query}"
        try:
            payload = verifier.verify(
                method=request.method,
                path_and_query=path_and_query,
                headers=request.headers,
                body=body,
            )
        except DispatchVerificationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        background_tasks.add_task(
            _run_handler,
            payload,
            handler,
            client_factory,
            enforce_url_security,
            resolved_allowed_origins,
            allow_insecure_local_urls,
        )
        return {"status": "accepted", "runId": payload.run_id}

    for prefix in resolved_route_prefixes:
        fastapi_app.add_api_route(f"{prefix}/health", health, methods=["GET"])
        fastapi_app.add_api_route(f"{prefix}/dispatch", dispatch, methods=["POST"])

    return fastapi_app


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Dispatch body is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Dispatch body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _run_handler(
    payload: ActionDispatchPayload,
    handler: Handler,
    client_factory: ClientFactory | None,
    enforce_url_security: bool,
    allowed_callback_origins: set[str] | None,
    allow_insecure_local_urls: bool,
) -> None:
    client = (
        client_factory(payload)
        if client_factory
        else ActionClient.from_dispatch(
            payload,
            enforce_url_security=enforce_url_security,
            allowed_callback_origins=allowed_callback_origins,
            allow_insecure_local_urls=allow_insecure_local_urls,
        )
    )
    async with client:
        context = ActionContext(payload=payload, client=client)
        try:
            await handler(context)
        except ActionCancelled:
            logger.info("LAREX Action run %s cancelled", payload.run_id)
            try:
                await context.cancelled()
            except Exception:
                logger.exception(
                    "Could not acknowledge cancelled LAREX Action run %s", payload.run_id
                )
        except Exception as exc:
            logger.exception("LAREX Action run %s failed", payload.run_id)
            try:
                await context.fail("Processor failed", log=f"{exc.__class__.__name__}: {exc}")
            except Exception:
                logger.exception("Could not report failed LAREX Action run %s", payload.run_id)


def _resolve_allowed_origins(
    explicit: Iterable[str] | None,
    env_name: str,
) -> set[str] | None:
    if explicit is not None:
        return {origin for origin in explicit if origin}
    raw = os.getenv(env_name)
    if not raw:
        return None
    return {origin.strip() for origin in raw.split(",") if origin.strip()}


def _resolve_route_prefixes(
    explicit: Iterable[str] | None,
    env_name: str,
) -> list[str]:
    raw_prefixes = explicit
    if raw_prefixes is None:
        raw = os.getenv(env_name)
        raw_prefixes = raw.split(",") if raw else []

    prefixes = [""]
    seen = {""}
    for raw_prefix in raw_prefixes:
        prefix = _normalize_route_prefix(raw_prefix)
        if prefix not in seen:
            prefixes.append(prefix)
            seen.add(prefix)
    return prefixes


def _normalize_route_prefix(value: str) -> str:
    prefix = value.strip()
    if not prefix or prefix == "/":
        return ""
    if "?" in prefix or "#" in prefix:
        raise ValueError("route prefixes must not include query strings or fragments")
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    normalized = prefix.rstrip("/")
    if "//" in normalized:
        raise ValueError("route prefixes must not contain empty path segments")
    return normalized
