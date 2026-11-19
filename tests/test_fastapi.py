from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from larex_actions import ActionClient, ActionContext, ParameterChoice, RetryPolicy
from larex_actions.fastapi import create_larex_action_app

from .conftest import (
    PROCESSOR_ID,
    SECRET,
    dispatch_payload,
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
async def test_fastapi_adapter_passes_retry_policy_to_the_client() -> None:
    graces: list[float] = []

    async def process(ctx: ActionContext) -> None:
        graces.append(ctx.client.heartbeat_failure_grace)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        retry=RetryPolicy(heartbeat_failure_grace=300),
    )
    body, headers = signed_dispatch()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)

    assert response.status_code == 200
    assert graces == [300.0]


def test_fastapi_adapter_rejects_retry_policy_with_client_factory() -> None:
    async def process(ctx: ActionContext) -> None:
        return None

    with pytest.raises(ValueError, match="client_factory"):
        create_larex_action_app(
            processor_id=PROCESSOR_ID,
            dispatch_secret=SECRET,
            handler=process,
            client_factory=ActionClient.from_dispatch,
            retry=RetryPolicy(),
        )


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
            "runReports": True,
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
async def test_fastapi_adapter_sends_choice_defaults_only_when_larex_supports_them() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("discovery must not run the processor")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        parameter_value_providers={
            "models": lambda: [
                ParameterChoice(value="model-a", label="Model A", default=True),
                {"value": "model-b", "label": "Model B"},
            ]
        },
    )
    legacy_body, legacy_headers = signed_parameter_values()
    body, headers = signed_parameter_values(
        payload=parameter_values_payload(
            capabilities={"parameterChoiceDefaults": True, "futureCapability": True}
        ),
        nonce="parameter-values-nonce-2",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        legacy = await client.post("/parameter-values", content=legacy_body, headers=legacy_headers)
        response = await client.post("/parameter-values", content=body, headers=headers)

    assert legacy.json()["values"]["models"] == [
        {"value": "model-a", "label": "Model A"},
        {"value": "model-b", "label": "Model B"},
    ]
    assert response.json()["values"]["models"] == [
        {"value": "model-a", "label": "Model A", "default": True},
        {"value": "model-b", "label": "Model B"},
    ]


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
async def test_fastapi_adapter_queues_dispatches_beyond_concurrency_limit() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def process(_ctx: ActionContext) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        active -= 1

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        max_concurrent_runs=1,
    )
    first_body, first_headers = signed_dispatch(
        payload=dispatch_payload(runId="run-1"), nonce="nonce-1"
    )
    second_body, second_headers = signed_dispatch(
        payload=dispatch_payload(runId="run-2"), nonce="nonce-2"
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = asyncio.create_task(
            client.post("/dispatch", content=first_body, headers=first_headers)
        )
        await started.wait()
        second = asyncio.create_task(
            client.post("/dispatch", content=second_body, headers=second_headers)
        )
        await asyncio.sleep(0.05)
        assert active == 1
        release.set()
        responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [200, 200]
    assert max_active == 1


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
