from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from larex_actions import (
    ActionClient,
    PageOutcome,
    PageResultRejected,
    ResultBuilder,
    ResultSubmissionError,
    RunIssue,
    RunReport,
    RunReportsUnsupported,
)


def client_for(http: httpx.AsyncClient, *, reports: bool = True) -> ActionClient:
    return ActionClient(
        pull_url="https://larex.example/input",
        heartbeat_url="https://larex.example/heartbeat",
        result_url="https://larex.example/results",
        secret="secret",
        supports_incremental_page_results=True,
        supports_run_reports=reports,
        client=http,
        result_retry_backoff=0,
    )


def manifest_from(request: httpx.Request) -> dict[str, Any]:
    body = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
    )
    return json.loads(next(body.iter_parts()).get_content())


@pytest.mark.parametrize("status", ["FAILED", "SKIPPED"])
def test_failed_and_skipped_pages_require_reason(status: str) -> None:
    with pytest.raises(ValidationError, match="reason"):
        PageOutcome.model_validate({"pageId": "p1", "status": status})


def test_report_rejects_duplicate_pages_and_blank_warnings() -> None:
    with pytest.raises(ValidationError, match="unique"):
        RunReport(pageOutcomes=[PageOutcome(pageId="p1")] * 2)
    with pytest.raises(ValidationError, match="blank"):
        PageOutcome(pageId="p1", warnings=[" "])


@pytest.mark.asyncio
async def test_failed_page_acknowledgement_then_completion() -> None:
    manifests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        manifests.append(manifest_from(request))
        return httpx.Response(200, json={"status": "COMPLETED_WITH_ISSUES"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = client_for(http)
        await client.submit_page_results(
            "p1", outcome=PageOutcome(pageId="p1", status="FAILED", message="Invalid XML")
        )
        await client.complete(run_report=RunReport(issues=[RunIssue(message="Some pages failed")]))
    assert manifests[0]["status"] == "running"
    assert manifests[0]["files"] == []
    assert manifests[0]["pageOutcome"]["status"] == "FAILED"
    assert manifests[1]["status"] == "completed"
    assert manifests[1]["runReport"]["issues"][0]["message"] == "Some pages failed"


@pytest.mark.asyncio
async def test_bulk_report_and_mutated_models_revalidated() -> None:
    manifests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        manifests.append(manifest_from(request))
        return httpx.Response(200, json={})

    report = RunReport(pageOutcomes=[PageOutcome(pageId="p1")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = client_for(http)
        await client.complete(run_report=report)
        report.page_outcomes.append(PageOutcome(pageId="p1"))
        with pytest.raises(ValidationError, match="unique"):
            await client.complete(run_report=report)
    assert len(manifests) == 1
    assert manifests[0]["runReport"]["schemaVersion"] == 1


@pytest.mark.asyncio
async def test_new_reporting_does_not_silently_downgrade_on_old_server() -> None:
    async with httpx.AsyncClient() as http:
        client = client_for(http, reports=False)
        with pytest.raises(RunReportsUnsupported):
            await client.complete(run_report=RunReport())
        with pytest.raises(RunReportsUnsupported):
            await client.submit_page_results("p1", outcome=PageOutcome(pageId="p1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["ACTION_PAGE_RESULT_REJECTED", "OTHER_ERROR"])
async def test_only_durable_page_rejection_is_recoverable(code: str) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(422, json={"code": code, "message": "Invalid output"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = client_for(http)
        expected = (
            PageResultRejected if code == "ACTION_PAGE_RESULT_REJECTED" else ResultSubmissionError
        )
        with pytest.raises(expected) as error:
            await client.submit_page_results("p1")
        assert isinstance(error.value, PageResultRejected) == (
            code == "ACTION_PAGE_RESULT_REJECTED"
        )
        if code == "ACTION_PAGE_RESULT_REJECTED":
            bulk = ResultBuilder()
            bulk.add_xml_bytes("p1", b"<PcGts/>", "page.xml")
            with pytest.raises(ValueError, match="Incremental"):
                await client.complete(bulk)
    assert attempts == 1


@pytest.mark.asyncio
async def test_fatal_heartbeat_can_include_run_level_report() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"cancelRequested": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await client_for(http).fail(
            "Model failed", run_report=RunReport(issues=[RunIssue(message="See logs")])
        )
    assert sent[0]["status"] == "failed"
    assert sent[0]["runReport"]["issues"][0]["message"] == "See logs"
