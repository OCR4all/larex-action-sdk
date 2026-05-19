from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from larex_actions import (
    ActionCancelled,
    ActionClient,
    ActionContext,
    ActionInput,
    ActionUrlSecurityError,
    ResultBuilder,
)

from .conftest import dispatch_payload


@pytest.mark.asyncio
async def test_client_pull_heartbeat_download_and_complete(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer run-secret"
        if request.url.path.endswith("/input"):
            return httpx.Response(
                200,
                json={
                    "protocolVersion": 1,
                    "runId": "run-1",
                    "processorKey": "mock-image-copy",
                    "projectId": "project-1",
                    "parameters": {},
                    "pages": [
                        {
                            "id": "page-1",
                            "name": "001",
                            "images": [],
                            "xml": [
                                {
                                    "id": "xml-1",
                                    "fileName": "001.xml",
                                    "variant": "default",
                                    "mimeType": "application/xml",
                                    "downloadUrl": "https://larex.example/file/xml-1",
                                }
                            ],
                        }
                    ],
                    "cancelRequested": False,
                },
            )
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancelRequested": False})
        if request.url.path == "/file/xml-1":
            return httpx.Response(200, content=b"<PcGts/>")
        if request.url.path.endswith("/results"):
            body = request.content
            assert b"manifest.json" in body
            assert b'"protocolVersion":1' in body
            assert b"page-1" in body
            assert b"copy.xml" in body
            return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
        )
        action_input = await client.pull_input()
        heartbeat = await client.heartbeat(50, "Halfway", raise_on_cancel=True)
        xml_bytes = await client.download_bytes(action_input.pages[0].xml[0])
        xml_path = await client.download_to_path(
            action_input.pages[0].xml[0],
            tmp_path / "copy.xml",
        )
        results = ResultBuilder()
        results.add_xml_bytes("page-1", xml_bytes, "copy.xml")
        await client.complete(results, "Done")

    assert heartbeat.cancel_requested is False
    assert xml_path.read_bytes() == b"<PcGts/>"
    assert [request.url.path for request in requests] == [
        "/public/actions/runs/run-1/input",
        "/public/actions/runs/run-1/heartbeat",
        "/file/xml-1",
        "/file/xml-1",
        "/public/actions/runs/run-1/results",
    ]


@pytest.mark.asyncio
async def test_heartbeat_raise_on_cancel() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cancelRequested": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(ActionCancelled):
            await client.heartbeat(raise_on_cancel=True)


def test_result_builder_manifest_and_paths(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"png")
    results = ResultBuilder()
    results.add_image_path("page-1", image_path, variant="copy", mime_type="image/png")
    results.add_xml_bytes("page-1", b"<PcGts/>", "page-copy")

    manifest = results.manifest(message="Done")

    assert manifest.message == "Done"
    assert manifest.protocol_version == 1
    assert [file.type for file in manifest.files] == ["image", "xml"]
    assert manifest.files[1].file_name == "page-copy.xml"


def test_action_input_parses_target_metadata() -> None:
    action_input = ActionInput.model_validate(
        {
            "protocolVersion": 1,
            "runId": "run-1",
            "processorKey": "ocr",
            "projectId": "project-1",
            "parameters": {},
            "pages": [],
            "targetSelection": {
                "type": "TEXT_LINE",
                "pages": [
                    {
                        "pageId": "page-1",
                        "regions": [],
                        "textLines": [
                            {
                                "id": "line-1",
                                "parentRegionId": "region-1",
                                "textContentVariants": [{"index": 0, "text": "old"}],
                            }
                        ],
                    }
                ],
            },
            "cancelRequested": False,
        }
    )

    target = action_input.target_selection
    assert target is not None
    assert action_input.target is target
    assert target.type == "TEXT_LINE"
    assert target.pages[0].text_lines[0].parent_region_id == "region-1"


def test_client_rejects_cross_origin_callback_urls() -> None:
    with pytest.raises(ActionUrlSecurityError, match="same origin"):
        ActionClient.from_dispatch(
            dispatch_payload_model(
                heartbeatUrl="https://evil.example/public/actions/runs/run-1/heartbeat"
            )
        )


def test_client_rejects_insecure_external_callback_url() -> None:
    with pytest.raises(ActionUrlSecurityError, match="https"):
        ActionClient.from_dispatch(
            dispatch_payload_model(
                pullUrl="http://larex.example/public/actions/runs/run-1/input",
                heartbeatUrl="http://larex.example/public/actions/runs/run-1/heartbeat",
                resultUrl="http://larex.example/public/actions/runs/run-1/results",
            )
        )


def test_client_allows_insecure_local_callback_url() -> None:
    client = ActionClient.from_dispatch(
        dispatch_payload_model(
            pullUrl="http://app:8080/public/actions/runs/run-1/input",
            heartbeatUrl="http://app:8080/public/actions/runs/run-1/heartbeat",
            resultUrl="http://app:8080/public/actions/runs/run-1/results",
        )
    )

    assert client.pull_url.startswith("http://app:8080/")


def test_client_rejects_callback_origin_outside_allowlist() -> None:
    with pytest.raises(ActionUrlSecurityError, match="not in allowed_callback_origins"):
        ActionClient.from_dispatch(
            dispatch_payload_model(),
            allowed_callback_origins={"https://other.example"},
        )


@pytest.mark.asyncio
async def test_context_validates_typed_parameters() -> None:
    class Parameters(BaseModel):
        threshold: float

    payload = dispatch_payload_model()
    async with ActionClient.from_dispatch(payload) as client:
        context = ActionContext(payload=payload, client=client)

        assert context.parameters_as(Parameters).threshold == 0.5


@pytest.mark.asyncio
async def test_context_step_emits_start_and_complete_logs() -> None:
    heartbeat_payloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        heartbeat_payloads.append(request.content.decode())
        return httpx.Response(200, json={"cancelRequested": False})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        context = ActionContext(payload=payload, client=client)

        async with context.step("Copy XML", progress_percent=30):
            pass

    assert len(heartbeat_payloads) == 2
    assert "step:start Copy XML" in heartbeat_payloads[0]
    assert "step:complete Copy XML" in heartbeat_payloads[1]


@pytest.mark.asyncio
async def test_client_rejects_cross_origin_download_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "protocolVersion": 1,
                "runId": "run-1",
                "processorKey": "mock-image-copy",
                "projectId": "project-1",
                "parameters": {},
                "pages": [
                    {
                        "id": "page-1",
                        "name": "001",
                        "images": [
                            {
                                "id": "image-1",
                                "fileName": "001.png",
                                "mimeType": "image/png",
                                "downloadUrl": "https://evil.example/file/image-1",
                            }
                        ],
                        "xml": [],
                    }
                ],
                "cancelRequested": False,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        action_input = await client.pull_input()
        with pytest.raises(ActionUrlSecurityError, match="does not match trusted LAREX origin"):
            await client.download_bytes(action_input.pages[0].images[0])


def dispatch_payload_model(**overrides: Any):
    from larex_actions import ActionDispatchPayload

    return ActionDispatchPayload.model_validate(dispatch_payload(**overrides))
