from __future__ import annotations

# pyright: reportUnusedFunction=false
import logging
import os
from collections.abc import Awaitable, Callable

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
) -> FastAPI:
    resolved_secret = dispatch_secret or os.getenv(dispatch_secret_env)
    if not resolved_secret:
        raise ValueError(f"Dispatch secret is not configured: {dispatch_secret_env}")

    verifier = DispatchVerifier(
        processor_id=processor_id,
        dispatch_secret=resolved_secret,
        nonce_store=nonce_store,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )
    fastapi_app = app or FastAPI(title=f"LAREX Action Processor: {processor_id}")

    @fastapi_app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @fastapi_app.post("/dispatch")
    async def dispatch(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
        body = await request.body()
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

        background_tasks.add_task(_run_handler, payload, handler, client_factory)
        return {"status": "accepted", "runId": payload.run_id}

    return fastapi_app


async def _run_handler(
    payload: ActionDispatchPayload,
    handler: Handler,
    client_factory: ClientFactory | None,
) -> None:
    client = client_factory(payload) if client_factory else ActionClient.from_dispatch(payload)
    async with client:
        context = ActionContext(payload=payload, client=client)
        try:
            await handler(context)
        except ActionCancelled:
            logger.info("LAREX Action run %s cancelled", payload.run_id)
        except Exception as exc:
            logger.exception("LAREX Action run %s failed", payload.run_id)
            try:
                await context.fail("Processor failed", log=f"{exc.__class__.__name__}: {exc}")
            except Exception:
                logger.exception("Could not report failed LAREX Action run %s", payload.run_id)
