from __future__ import annotations

import json

import httpx
import pytest

from larex_actions import ActionClient, ActionContext
from larex_actions.fastapi import create_larex_action_app

from .conftest import PROCESSOR_ID, SECRET, signed_dispatch


@pytest.mark.asyncio
async def test_fastapi_adapter_accepts_dispatch_and_runs_handler() -> None:
    calls: list[str] = []

    async def process(ctx: ActionContext) -> None:
        calls.append(ctx.run_id)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "runId": "run-1"}
    assert calls == ["run-1"]


@pytest.mark.asyncio
async def test_fastapi_adapter_rejects_invalid_dispatch() -> None:
    async def process(ctx: ActionContext) -> None:
        raise AssertionError("should not run")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
    )
    body, headers = signed_dispatch()
    headers["X-LAREX-Action-Signature"] = "v1=bad"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_fastapi_adapter_rejects_large_dispatch_body() -> None:
    async def process(ctx: ActionContext) -> None:
        raise AssertionError("should not run")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        max_dispatch_body_bytes=8,
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_fastapi_adapter_reports_handler_exception() -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    async def process(ctx: ActionContext) -> None:
        raise RuntimeError("boom")

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            heartbeat_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"cancelRequested": False})
        return httpx.Response(404)

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        client_factory=lambda payload: ActionClient.from_dispatch(payload, client=async_client),
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    await async_client.aclose()
    assert response.status_code == 200
    assert heartbeat_payloads


@pytest.mark.asyncio
async def test_fastapi_adapter_acknowledges_cooperative_cancellation() -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    async def process(ctx: ActionContext) -> None:
        await ctx.check_cancelled()

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            heartbeat_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"cancelRequested": True})
        return httpx.Response(404)

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        client_factory=lambda payload: ActionClient.from_dispatch(payload, client=async_client),
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    await async_client.aclose()
    assert response.status_code == 200
    assert [payload["status"] for payload in heartbeat_payloads] == ["running", "cancelled"]
