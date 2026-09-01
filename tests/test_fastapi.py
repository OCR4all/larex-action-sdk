from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from larex_actions import ActionClient, ActionContext, ParameterChoice
from larex_actions.fastapi import create_larex_action_app

from .conftest import (
    PROCESSOR_ID,
    SECRET,
    parameter_values_payload,
    signed_dispatch,
    signed_parameter_values,
    signed_preflight,
)


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
async def test_fastapi_adapter_accepts_authenticated_preflight() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("preflight must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
    )
    body, headers = signed_preflight()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/preflight", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "protocolVersion": 1,
        "requestId": "preflight-1",
        "processorId": PROCESSOR_ID,
        "capabilities": {
            "incrementalPageResults": True,
            "customFileResults": True,
            "parameterValueDiscovery": False,
        },
    }


@pytest.mark.asyncio
async def test_fastapi_adapter_discovers_typed_parameter_values() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("discovery must not run the processor")

    async def models():
        return [
            ParameterChoice(value="model-a", label="Model A"),
            {"value": 2, "label": "Model 2"},
            True,
        ]

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        parameter_value_providers={"models": models},
    )
    body, headers = signed_parameter_values()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/parameter-values", content=body, headers=headers)
        preflight_body, preflight_headers = signed_preflight(nonce="other-nonce")
        preflight = await client.post(
            "/preflight", content=preflight_body, headers=preflight_headers
        )

    assert response.status_code == 200
    assert response.json()["values"]["models"] == [
        {"value": "model-a", "label": "Model A"},
        {"value": 2, "label": "Model 2"},
        {"value": True, "label": "True"},
    ]
    assert preflight.json()["capabilities"]["parameterValueDiscovery"] is True


@pytest.mark.asyncio
async def test_fastapi_adapter_supports_sync_provider_on_prefixed_route() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("discovery must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        route_prefixes=["/ner"],
        parameter_value_providers={"models": lambda: ["model-a"]},
    )
    body, headers = signed_parameter_values(path_and_query="/ner/parameter-values")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/ner/parameter-values", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["values"]["models"] == [{"value": "model-a", "label": "model-a"}]


@pytest.mark.asyncio
async def test_fastapi_adapter_rejects_unknown_or_duplicate_parameter_values() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("discovery must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        parameter_value_providers={"models": lambda: ["same", "same"]},
    )
    duplicate_body, duplicate_headers = signed_parameter_values()
    unknown_body, unknown_headers = signed_parameter_values(
        payload=parameter_values_payload(providers=["unknown"]),
        nonce="unknown-nonce",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        duplicate = await client.post(
            "/parameter-values", content=duplicate_body, headers=duplicate_headers
        )
        unknown = await client.post(
            "/parameter-values", content=unknown_body, headers=unknown_headers
        )

    assert duplicate.status_code == 500
    assert duplicate.json() == {"detail": "Parameter value discovery failed"}
    assert unknown.status_code == 400


@pytest.mark.asyncio
async def test_fastapi_adapter_authenticates_parameter_value_discovery() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("discovery must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        parameter_value_providers={"models": lambda: ["model-a"]},
    )
    body, headers = signed_parameter_values(secret="wrong-secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/parameter-values", content=body, headers=headers)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_fastapi_adapter_rejects_preflight_with_wrong_secret() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("preflight must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
    )
    body, headers = signed_preflight(secret="wrong-secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/preflight", content=body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Dispatch signature mismatch"}


@pytest.mark.asyncio
async def test_fastapi_adapter_reports_busy_while_concurrency_slot_is_occupied() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def process(_ctx: ActionContext) -> None:
        started.set()
        await release.wait()

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        max_concurrent_runs=1,
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        dispatch_task = asyncio.create_task(client.post("/dispatch", content=body, headers=headers))
        await started.wait()
        ready_response = await client.get("/ready")
        release.set()
        dispatch_response = await dispatch_task

    assert ready_response.status_code == 503
    assert ready_response.json() == {"status": "busy"}
    assert dispatch_response.status_code == 200


@pytest.mark.asyncio
async def test_fastapi_adapter_accepts_prefixed_dispatch_route() -> None:
    calls: list[str] = []

    async def process(ctx: ActionContext) -> None:
        calls.append(ctx.run_id)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        route_prefixes=["/kraken"],
    )
    body, headers = signed_dispatch(path_and_query="/kraken/dispatch")
    preflight_body, preflight_headers = signed_preflight(path_and_query="/kraken/preflight")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/kraken/dispatch", content=body, headers=headers)
        health_response = await client.get("/kraken/health")
        preflight_response = await client.post(
            "/kraken/preflight",
            content=preflight_body,
            headers=preflight_headers,
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "runId": "run-1"}
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert preflight_response.status_code == 200
    assert calls == ["run-1"]


@pytest.mark.asyncio
async def test_fastapi_adapter_keeps_unprefixed_dispatch_route_with_prefixes() -> None:
    calls: list[str] = []

    async def process(ctx: ActionContext) -> None:
        calls.append(ctx.run_id)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        route_prefixes=["/kraken"],
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    assert response.status_code == 200
    assert calls == ["run-1"]


@pytest.mark.asyncio
async def test_fastapi_adapter_rejects_prefixed_dispatch_with_unprefixed_signature() -> None:
    async def process(ctx: ActionContext) -> None:
        raise AssertionError("should not run")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        route_prefixes=["/kraken"],
    )
    body, headers = signed_dispatch(path_and_query="/dispatch")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/kraken/dispatch", content=body, headers=headers)

    assert response.status_code == 401


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
