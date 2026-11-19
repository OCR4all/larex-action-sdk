from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from collections import Counter
from collections.abc import AsyncIterator
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
    CustomFileResultsUnsupported,
    IncrementalResultsUnsupported,
    ResultBuilder,
    ResultSubmissionError,
    RetryPolicy,
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
    heartbeat_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        heartbeat_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"cancelRequested": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(ActionCancelled):
            await client.heartbeat(raise_on_cancel=True)

    assert [payload["status"] for payload in heartbeat_payloads] == ["running", "cancelled"]


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


@pytest.mark.asyncio
async def test_client_submits_incremental_page_result_and_empty_completion() -> None:
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        return httpx.Response(200, json={"id": "run-1", "status": "RUNNING"})

    payload = dispatch_payload_model(
        capabilities={"incrementalPageResults": True},
    )
    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        await client.submit_page_results("page-1", results, "Page done")
        await client.complete(message="All done")

    assert b'"status":"running"' in request_bodies[0]
    assert b'"pageId":"page-1"' in request_bodies[0]
    assert b'"status":"completed"' in request_bodies[1]
    assert b'"files":[]' in request_bodies[1]


@pytest.mark.asyncio
async def test_result_submission_retries_and_reopens_path_files(tmp_path: Path) -> None:
    result_path = tmp_path / "page.xml"
    result_path.write_bytes(b"<PcGts/>")
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        if len(request_bodies) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": "run-1", "status": "RUNNING"})

    results = ResultBuilder()
    results.add_xml_path("page-1", result_path)
    payload = dispatch_payload_model(capabilities={"incrementalPageResults": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload,
            client=http_client,
            retry=RetryPolicy(backoff=0),
        )
        await client.submit_page_results("page-1", results)

    assert len(request_bodies) == 2
    assert all(b"<PcGts/>" in body for body in request_bodies)


@pytest.mark.asyncio
async def test_result_submission_keeps_retrying_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 5:
            raise httpx.ConnectError("Connection refused")
        if attempts <= 8:
            return httpx.Response(502)
        return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})

    monkeypatch.setattr("larex_actions.client.asyncio.sleep", fake_sleep)
    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        await client.complete(results, "Done")

    assert attempts == 9
    assert len(sleeps) == 8
    assert all(0 <= delay <= 30 for delay in sleeps)


@pytest.mark.asyncio
async def test_result_submission_stops_retrying_when_window_has_passed() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("Connection refused")

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(result_window=0)
        )
        with pytest.raises(httpx.ConnectError):
            await client.complete(results, "Done")

    assert attempts == 1


@pytest.mark.asyncio
async def test_result_retry_delay_never_exceeds_remaining_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        if not sleeps:
            return httpx.Response(503, headers={"Retry-After": "30"})
        return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})

    monkeypatch.setattr("larex_actions.client.asyncio.sleep", fake_sleep)
    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(result_window=5)
        )
        await client.complete(results, "Done")

    assert len(sleeps) == 1
    assert sleeps[0] <= 5


@pytest.mark.asyncio
async def test_result_max_attempts_still_caps_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(result_max_attempts=3, backoff=0),
        )
        with pytest.raises(ResultSubmissionError):
            await client.complete(results, "Done")

    assert attempts == 3


@pytest.mark.asyncio
async def test_result_retries_warn_once_per_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        with caplog.at_level(logging.WARNING, logger="larex_actions.client"):
            await client.complete(results, "Done")

    warnings = [r.getMessage() for r in caplog.records if r.name == "larex_actions.client"]
    assert len(warnings) == 1
    assert "Could not deliver complete for LAREX run run-1 (HTTP 503)" in warnings[0]


class _FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


@pytest.mark.asyncio
async def test_result_retries_stop_once_larex_timeout_has_passed_since_last_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock(1_000.0)
    monkeypatch.setattr("larex_actions.client.time", clock)
    monkeypatch.setattr("larex_actions.client.asyncio.sleep", clock.sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancelRequested": False})
        raise httpx.ConnectError("Connection refused")

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_failure_grace=600),
        )
        clock.now = 1_500.0
        await client.heartbeat()
        clock.now = 2_000.0
        with pytest.raises(httpx.ConnectError):
            await client.complete(results, "Done")

    assert clock.now == pytest.approx(2_100.0)


@pytest.mark.asyncio
async def test_result_retries_wait_for_larex_with_infinite_grace(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = _FakeClock(1_000.0)
    monkeypatch.setattr("larex_actions.client.time", clock)
    monkeypatch.setattr("larex_actions.client.asyncio.sleep", clock.sleep)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 500:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_failure_grace=math.inf),
        )
        with caplog.at_level(logging.WARNING, logger="larex_actions.client"):
            await client.complete(results, "Done")

    assert attempts == 501
    assert clock.now - 1_000.0 > 3_600
    warnings = [r.getMessage() for r in caplog.records if r.name == "larex_actions.client"]
    assert warnings
    assert all(message.endswith("retrying until LAREX answers") for message in warnings)


@pytest.mark.asyncio
async def test_result_window_overrides_larex_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock(1_000.0)
    monkeypatch.setattr("larex_actions.client.time", clock)
    monkeypatch.setattr("larex_actions.client.asyncio.sleep", clock.sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_failure_grace=0, result_window=120),
        )
        with pytest.raises(httpx.ConnectError):
            await client.complete(results, "Done")

    assert clock.now == pytest.approx(1_120.0)


@pytest.mark.asyncio
async def test_result_upload_raises_when_larex_reports_the_run_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "run-1",
                "status": "FAILED",
                "errorMessage": "Action heartbeat timed out",
            },
        )

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(ResultSubmissionError, match="Action heartbeat timed out"):
            await client.complete(results, "Done")


@pytest.mark.asyncio
async def test_heartbeats_use_the_short_heartbeat_timeout() -> None:
    timeouts: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={"cancelRequested": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_request_timeout=12),
        )
        await client.heartbeat(10)

    assert timeouts == [{"connect": 5.0, "read": 12.0, "write": 12.0, "pool": 12.0}]


@pytest.mark.asyncio
async def test_deprecated_retry_arguments_still_apply() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.warns(DeprecationWarning, match="RetryPolicy"):
            client = ActionClient.from_dispatch(
                dispatch_payload_model(),
                client=http_client,
                result_max_attempts=2,
                result_retry_backoff=0,
            )
        with pytest.raises(ResultSubmissionError):
            await client.complete(results, "Done")

    assert attempts == 2


@pytest.mark.asyncio
async def test_transport_debug_logging_covers_sdk_operations(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/input"):
            return httpx.Response(
                200,
                json={
                    "protocolVersion": 1,
                    "runId": "run-1",
                    "processorKey": "logging-test",
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
                                    "mimeType": "application/xml",
                                    "downloadUrl": "https://larex.example/file/xml-1",
                                }
                            ],
                        }
                    ],
                },
            )
        if request.url.path.endswith("/heartbeat"):
            heartbeat_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"cancelRequested": False})
        if request.url.path == "/file/xml-1":
            return httpx.Response(200, content=b"<PcGts/>")
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    payload = dispatch_payload_model(capabilities={"incrementalPageResults": True})
    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "result.xml")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        with caplog.at_level(logging.DEBUG, logger="larex_actions.transport"):
            action_input = await client.pull_input()
            await client.heartbeat(25, "Working")
            await client.download_bytes(action_input.pages[0].xml[0])
            await client.download_to_path(action_input.pages[0].xml[0], tmp_path / "download.xml")
            await client.submit_page_results("page-1", results)
            await client.complete()
            await client.fail("Failed intentionally")

    messages = [
        record.getMessage() for record in caplog.records if record.name == "larex_actions.transport"
    ]
    joined = "\n".join(messages)
    for operation in (
        "pull_input",
        "heartbeat",
        "download_bytes",
        "download_to_path",
        "submit_page_results",
        "complete",
        "fail",
    ):
        assert f'operation="{operation}"' in joined
    assert 'run_id="run-1"' in joined
    assert 'page_id="page-1"' in joined
    assert 'result_status="running"' in joined
    assert 'result_status="completed"' in joined
    assert 'result_status="failed"' in joined
    assert "result_file_count=1" in joined
    assert 'result_file_types="xml:1"' in joined
    assert "http_status=200" in joined
    assert "attempt=1" in joined
    assert "duration_ms=" in joined
    assert all("log" not in payload for payload in heartbeat_payloads)
    assert "LAREX SDK transport" not in "\n".join(map(str, heartbeat_payloads))


@pytest.mark.asyncio
async def test_transport_logging_reports_retries_and_redacts_request_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    authorization_secret = "authorization-secret-must-not-appear"
    result_body_secret = b"page-xml-body-must-not-appear"
    result_message_secret = "result-message-must-not-appear"
    response_body_secret = "response-body-must-not-appear"
    sensitive_query = "signed-query-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(
            400,
            request=request,
            json={"message": response_body_secret},
        )

    payload = dispatch_payload_model(
        secret=authorization_secret,
        resultUrl=f"https://larex.example/results?signature={sensitive_query}",
    )
    results = ResultBuilder()
    results.add_xml_bytes("page-1", result_body_secret, "secret.xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload,
            client=http_client,
            retry=RetryPolicy(backoff=0),
        )
        with (
            caplog.at_level(logging.DEBUG, logger="larex_actions.transport"),
            pytest.raises(ResultSubmissionError),
        ):
            await client.complete(results, result_message_secret)

    messages = [
        record.getMessage() for record in caplog.records if record.name == "larex_actions.transport"
    ]
    joined = "\n".join(messages)
    assert 'operation="complete" event="retry"' in joined
    assert "attempt=1" in joined
    assert "http_status=503" in joined
    assert 'operation="complete" event="failed"' in joined
    assert "attempt=2" in joined
    assert "http_status=400" in joined
    assert 'error_type="ResultSubmissionError"' in joined
    assert "https://larex.example" not in joined
    for secret in (
        authorization_secret,
        result_body_secret.decode(),
        result_message_secret,
        response_body_secret,
        sensitive_query,
    ):
        assert secret not in joined


@pytest.mark.asyncio
async def test_transport_logging_does_not_include_transport_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exception_secret = "transport-exception-secret-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(exception_secret, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with (
            caplog.at_level(logging.DEBUG, logger="larex_actions.transport"),
            pytest.raises(httpx.ConnectError),
        ):
            await client.pull_input()

    joined = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "larex_actions.transport"
    )
    assert 'operation="pull_input" event="failed"' in joined
    assert 'error_type="ConnectError"' in joined
    assert exception_secret not in joined


@pytest.mark.asyncio
async def test_result_submission_does_not_retry_client_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            request=request,
            json={
                "message": "Missing result file part: file_22",
                "debug": "Bearer internal-token-must-not-be-logged",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(
            ResultSubmissionError,
            match=r"HTTP 400 Bad Request.*Missing result file part: file_22",
        ) as caught:
            await client.complete()

    assert attempts == 1
    assert "internal-token-must-not-be-logged" not in str(caught.value)


@pytest.mark.asyncio
async def test_result_submission_error_is_bounded_and_redacts_bearer_tokens() -> None:
    response_text = "Bearer secret-token " + ("x" * 3_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            request=request,
            headers={"Content-Type": "text/plain"},
            text=response_text,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(ResultSubmissionError) as caught:
            await client.complete()

    message = str(caught.value)
    assert "secret-token" not in message
    assert "Bearer [REDACTED]" in message
    assert message.endswith("…")
    assert len(message) < 2_200


@pytest.mark.asyncio
async def test_client_rejects_incremental_results_without_server_capability() -> None:
    results = ResultBuilder()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(IncrementalResultsUnsupported):
            await client.submit_page_results("page-1", results)


@pytest.mark.asyncio
async def test_client_rejects_custom_files_without_server_capability() -> None:
    results = ResultBuilder()
    results.add_file_bytes(b"content", "result.txt", mime_type="text/plain")
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(CustomFileResultsUnsupported, match="customFileResults"):
            await client.complete(results)

    assert request_count == 0


@pytest.mark.asyncio
async def test_client_uploads_project_level_custom_file_with_capability() -> None:
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        return httpx.Response(200, json={"status": "COMPLETED"})

    payload = dispatch_payload_model(capabilities={"customFileResults": True})
    results = ResultBuilder()
    results.add_file_bytes(
        b'{"entity":"Ada"}\n',
        "entities.jsonl",
        mime_type="application/x-ndjson",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        response = await client.complete(results)

    assert response["status"] == "COMPLETED"
    assert b'"type":"file"' in request_bodies[0]
    assert b'"pageId"' not in request_bodies[0]
    assert b"application/x-ndjson" in request_bodies[0]


@pytest.mark.asyncio
async def test_incremental_custom_file_requires_matching_page_id() -> None:
    payload = dispatch_payload_model(
        capabilities={"incrementalPageResults": True, "customFileResults": True}
    )
    results = ResultBuilder()
    results.add_file_bytes(b"content", "result.txt", mime_type="text/plain")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        with pytest.raises(ValueError, match="match page_id"):
            await client.submit_page_results("page-1", results)


@pytest.mark.asyncio
async def test_client_rejects_mixed_page_incremental_builder() -> None:
    results = ResultBuilder()
    results.add_xml_bytes("page-2", b"<PcGts/>", "page.xml")
    payload = dispatch_payload_model(capabilities={"incrementalPageResults": True})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        with pytest.raises(ValueError, match="match page_id"):
            await client.submit_page_results("page-1", results)


@pytest.mark.asyncio
async def test_generic_upload_cannot_bypass_incremental_capability_gate() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(ValueError, match="submit_page_results"):
            await client.upload_results(ResultBuilder(), status="running")


@pytest.mark.asyncio
async def test_client_rejects_bulk_files_after_incremental_submission() -> None:
    payload = dispatch_payload_model(capabilities={"incrementalPageResults": True})
    first = ResultBuilder()
    bulk = ResultBuilder()
    bulk.add_xml_bytes("page-2", b"<PcGts/>", "page.xml")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    ) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        await client.submit_page_results("page-1", first)
        with pytest.raises(ValueError, match="without bulk"):
            await client.complete(bulk)


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
                        "regionIds": ["region-1"],
                        "textLineIds": ["line-1"],
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
    assert target.pages[0].region_ids == ["region-1"]
    assert target.pages[0].text_line_ids == ["line-1"]


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


@pytest.mark.parametrize("host", ["[2001:4860:4860::8888]", "[::ffff:8.8.8.8]"])
def test_client_rejects_insecure_public_ipv6_callback_url(host: str) -> None:
    with pytest.raises(ActionUrlSecurityError, match="https"):
        ActionClient.from_dispatch(
            dispatch_payload_model(
                pullUrl=f"http://{host}/public/actions/runs/run-1/input",
                heartbeatUrl=f"http://{host}/public/actions/runs/run-1/heartbeat",
                resultUrl=f"http://{host}/public/actions/runs/run-1/results",
            )
        )


@pytest.mark.parametrize("host", ["[::1]", "[fd00::1]", "[fe80::1]", "[::ffff:10.0.0.1]"])
def test_client_allows_insecure_local_ipv6_callback_url(host: str) -> None:
    client = ActionClient.from_dispatch(
        dispatch_payload_model(
            pullUrl=f"http://{host}:8080/public/actions/runs/run-1/input",
            heartbeatUrl=f"http://{host}:8080/public/actions/runs/run-1/heartbeat",
            resultUrl=f"http://{host}:8080/public/actions/runs/run-1/results",
        )
    )

    assert client.pull_url.startswith(f"http://{host}:8080/")


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
async def test_context_check_cancelled_acknowledges_cancellation() -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        heartbeat_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"cancelRequested": True})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        context = ActionContext(payload=payload, client=client)

        with pytest.raises(ActionCancelled):
            await context.check_cancelled()

    assert [payload["status"] for payload in heartbeat_payloads] == ["running", "cancelled"]


@pytest.mark.asyncio
async def test_check_cancelled_reuses_recent_heartbeat() -> None:
    heartbeat_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_count
        heartbeat_count += 1
        return httpx.Response(200, json={"cancelRequested": False})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client, cancel_check_interval=60)
        context = ActionContext(payload=payload, client=client)

        for _ in range(10):
            await context.check_cancelled()

    assert heartbeat_count == 1


@pytest.mark.asyncio
async def test_check_cancelled_polls_again_after_interval() -> None:
    heartbeat_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_count
        heartbeat_count += 1
        return httpx.Response(200, json={"cancelRequested": heartbeat_count >= 3})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client, cancel_check_interval=0)
        context = ActionContext(payload=payload, client=client)

        await context.check_cancelled()
        await context.check_cancelled()
        with pytest.raises(ActionCancelled):
            await context.check_cancelled()


@pytest.mark.asyncio
async def test_heartbeat_retries_dropped_connection() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if attempts == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"cancelRequested": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        heartbeat = await client.heartbeat(10, "Working")

    assert attempts == 3
    assert heartbeat.cancel_requested is False


@pytest.mark.asyncio
async def test_heartbeat_raises_after_max_attempts() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_attempts=2, heartbeat_failure_grace=0, backoff=0),
        )
        with pytest.raises(httpx.RemoteProtocolError):
            await client.heartbeat()

    assert attempts == 2


@pytest.mark.asyncio
async def test_check_cancelled_tolerates_unreachable_larex(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"cancelRequested": True})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload, client=http_client, cancel_check_interval=0, retry=RetryPolicy(backoff=0)
        )
        context = ActionContext(payload=payload, client=client)

        with caplog.at_level(logging.WARNING, logger="larex_actions.client"):
            await context.check_cancelled()
        with pytest.raises(ActionCancelled):
            await context.check_cancelled()

    assert "Could not reach LAREX for run" in caplog.text


@pytest.mark.asyncio
async def test_check_cancelled_raises_once_grace_has_passed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload,
            client=http_client,
            cancel_check_interval=0,
            retry=RetryPolicy(heartbeat_failure_grace=0, backoff=0),
        )
        context = ActionContext(payload=payload, client=client)

        with pytest.raises(httpx.HTTPStatusError):
            await context.check_cancelled()


@pytest.mark.asyncio
async def test_check_cancelled_raises_when_larex_rejects_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client, cancel_check_interval=0)
        context = ActionContext(payload=payload, client=client)

        with pytest.raises(httpx.HTTPStatusError):
            await context.check_cancelled()


@pytest.mark.asyncio
async def test_check_cancelled_waits_an_interval_after_a_failed_check() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("Connection refused")

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload,
            client=http_client,
            cancel_check_interval=60,
            retry=RetryPolicy(heartbeat_attempts=1),
        )
        context = ActionContext(payload=payload, client=client)

        for _ in range(5):
            await context.check_cancelled()

    assert attempts == 1


@pytest.mark.asyncio
async def test_running_heartbeat_tolerates_unreachable_larex(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        with caplog.at_level(logging.WARNING, logger="larex_actions.client"):
            heartbeat = await client.heartbeat(50, "Training", log="epoch 10")
            await client.heartbeat(raise_on_cancel=True)

    assert heartbeat.cancel_requested is False
    assert "heartbeat, ConnectError" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_raises_once_grace_has_passed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(heartbeat_failure_grace=0, backoff=0),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.heartbeat(50, "Training")


@pytest.mark.asyncio
async def test_heartbeat_raises_when_larex_rejects_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        with pytest.raises(httpx.HTTPStatusError):
            await client.heartbeat(50, "Training")


@pytest.mark.asyncio
async def test_failure_report_still_raises_when_larex_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        with pytest.raises(httpx.ConnectError):
            await client.heartbeat(status="failed", error_message="Boom")


@pytest.mark.asyncio
async def test_check_cancelled_short_circuits_after_known_cancellation() -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/input"):
            return httpx.Response(
                200,
                json={
                    "protocolVersion": 1,
                    "runId": "run-1",
                    "processorKey": "mock-image-copy",
                    "projectId": "project-1",
                    "parameters": {},
                    "pages": [],
                    "cancelRequested": True,
                },
            )
        if request.url.path.endswith("/heartbeat"):
            heartbeat_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"cancelRequested": True})
        raise AssertionError(f"Unexpected request: {request.url}")

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        context = ActionContext(payload=payload, client=client)

        await context.pull_input()
        with pytest.raises(ActionCancelled):
            await context.check_cancelled()

    assert [payload["status"] for payload in heartbeat_payloads] == ["cancelled"]


@pytest.mark.asyncio
async def test_client_rejects_result_upload_after_cancellation_request() -> None:
    heartbeat_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            heartbeat_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"cancelRequested": True})
        if request.url.path.endswith("/results"):
            raise AssertionError("results upload must not be attempted after cancellation")
        raise AssertionError(f"Unexpected request: {request.url}")

    results = ResultBuilder()
    results.add_xml_bytes("page-1", b"<PcGts/>", "copy.xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dispatch_payload_model(), client=http_client)
        await client.heartbeat()
        with pytest.raises(ActionCancelled):
            await client.complete(results, "Done")

    assert [payload["status"] for payload in heartbeat_payloads] == ["running", "cancelled"]


@pytest.mark.asyncio
async def test_context_run_subprocess_terminates_on_cancellation(tmp_path: Path) -> None:
    heartbeat_payloads: list[dict[str, object]] = []
    sentinel_path = tmp_path / "sentinel.txt"

    def subprocess_handles_sigterm() -> bool:
        return sentinel_path.exists() and sentinel_path.read_text(encoding="utf-8") == "started"

    def handler(request: httpx.Request) -> httpx.Response:
        heartbeat_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"cancelRequested": subprocess_handles_sigterm()})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client, cancel_check_interval=0)
        context = ActionContext(payload=payload, client=client)

        with pytest.raises(ActionCancelled):
            await context.run_subprocess(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, signal, sys, time\n"
                        "sentinel = pathlib.Path(sys.argv[1])\n"
                        "def handle(*_args):\n"
                        "    sentinel.write_text('terminated', encoding='utf-8')\n"
                        "    raise SystemExit(0)\n"
                        "signal.signal(signal.SIGTERM, handle)\n"
                        "sentinel.write_text('started', encoding='utf-8')\n"
                        "while True:\n"
                        "    time.sleep(1)\n"
                    ),
                    str(sentinel_path),
                ],
                timeout=30.0,
                terminate_grace_seconds=1.0,
                cancel_poll_seconds=0.05,
            )

    assert sentinel_path.read_text(encoding="utf-8") == "terminated"
    statuses = [payload["status"] for payload in heartbeat_payloads]
    assert len(statuses) >= 3
    assert set(statuses[:-1]) == {"running"}
    assert statuses[-1] == "cancelled"


@pytest.mark.asyncio
async def test_context_run_subprocess_terminates_on_timeout() -> None:
    heartbeat_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count > 50:
            return httpx.Response(500)
        return httpx.Response(200, json={"cancelRequested": False})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(payload, client=http_client)
        context = ActionContext(payload=payload, client=client)

        with pytest.raises(TimeoutError, match="Subprocess timed out"):
            await context.run_subprocess(
                [sys.executable, "-c", "import time\ntime.sleep(60)"],
                timeout=0.3,
                terminate_grace_seconds=1.0,
                cancel_poll_seconds=0.1,
            )

    assert heartbeat_count <= 10


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


def _input_with_xml_file() -> dict[str, Any]:
    return {
        "protocolVersion": 1,
        "runId": "run-1",
        "processorKey": "retry-test",
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
    }


@pytest.mark.asyncio
async def test_pull_input_and_downloads_retry_transient_failures(tmp_path: Path) -> None:
    calls: Counter[str] = Counter()

    async def interrupted_body() -> AsyncIterator[bytes]:
        yield b"<Pc"
        raise httpx.ReadError("Connection reset by peer")

    def handler(request: httpx.Request) -> httpx.Response:
        key = "input" if request.url.path.endswith("/input") else request.url.path
        calls[key] += 1
        attempt = calls[key]
        if key == "input":
            if attempt == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return httpx.Response(200, json=_input_with_xml_file())
        if request.url.path == "/file/xml-1":
            if attempt == 1:
                return httpx.Response(503)
            if attempt == 2:
                return httpx.Response(200, content=interrupted_body())
            return httpx.Response(200, content=b"<PcGts/>")
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        action_input = await client.pull_input()
        path = await client.download_to_path(action_input.pages[0].xml[0], tmp_path / "a.xml")
        content = await client.download_bytes(action_input.pages[0].xml[0])

    assert calls["input"] == 2
    assert path.read_bytes() == b"<PcGts/>"
    assert content == b"<PcGts/>"
    assert calls["/file/xml-1"] == 4


@pytest.mark.asyncio
async def test_downloads_raise_after_read_max_attempts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/input"):
            return httpx.Response(200, json=_input_with_xml_file())
        calls += 1
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(),
            client=http_client,
            retry=RetryPolicy(read_attempts=2, backoff=0),
        )
        action_input = await client.pull_input()
        with pytest.raises(httpx.HTTPStatusError):
            await client.download_bytes(action_input.pages[0].xml[0])

    assert calls == 2


@pytest.mark.asyncio
async def test_downloads_do_not_retry_client_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/input"):
            return httpx.Response(200, json=_input_with_xml_file())
        calls += 1
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dispatch_payload_model(), client=http_client, retry=RetryPolicy(backoff=0)
        )
        action_input = await client.pull_input()
        with pytest.raises(httpx.HTTPStatusError):
            await client.download_bytes(action_input.pages[0].xml[0])

    assert calls == 1


def dispatch_payload_model(**overrides: Any):
    from larex_actions import ActionDispatchPayload

    return ActionDispatchPayload.model_validate(dispatch_payload(**overrides))


@pytest.mark.parametrize(
    ("timeout_seconds", "policy", "expected"),
    [
        (2700, None, 2700.0),
        (2700, RetryPolicy(heartbeat_failure_grace=60), 60.0),
        (None, None, 1800.0),
        (0, None, 1800.0),
    ],
)
def test_heartbeat_failure_grace_follows_larex_timeout(
    timeout_seconds: int | None, policy: RetryPolicy | None, expected: float
) -> None:
    payload = dispatch_payload_model(heartbeatTimeoutSeconds=timeout_seconds)
    client = ActionClient.from_dispatch(payload, retry=policy)

    assert payload.heartbeat_timeout_seconds == timeout_seconds
    assert client.heartbeat_failure_grace == expected


@pytest.mark.asyncio
async def test_seconds_since_last_heartbeat_resets_on_success_only() -> None:
    reachable = True

    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(200, json={"cancelRequested": False})

    payload = dispatch_payload_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            payload, client=http_client, retry=RetryPolicy(backoff=0), cancel_check_interval=0
        )
        context = ActionContext(payload=payload, client=client)
        await asyncio.sleep(0.05)
        assert context.seconds_since_last_heartbeat >= 0.05

        await context.check_cancelled()
        assert context.seconds_since_last_heartbeat < 0.05

        reachable = False
        await asyncio.sleep(0.05)
        await context.check_cancelled()
        assert context.seconds_since_last_heartbeat >= 0.05
