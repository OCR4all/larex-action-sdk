from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from larex_actions import (
    ActionCancelled,
    ActionCapabilities,
    ActionClient,
    ActionContext,
    ActionDispatchPayload,
    ActionInput,
    EvaluationMetric,
    EvaluationReport,
    EvaluationReportsUnsupported,
    EvaluationTableRow,
    ResultBuilder,
)
from larex_actions.fastapi import create_larex_action_app

from .conftest import PROCESSOR_ID, SECRET, dispatch_payload, signed_dispatch, signed_preflight


def report(**overrides: Any) -> EvaluationReport:
    return EvaluationReport.model_validate(
        {
            "profile": "larex.ocr-recognition",
            "profileVersion": 1,
            "title": "Recognition quality",
            "summary": [{"key": "cer", "label": "CER", "value": 0.12}],
            **overrides,
        }
    )


def dataset_payload(kind: str = "EVALUATION", **overrides: Any) -> ActionDispatchPayload:
    return ActionDispatchPayload.model_validate(
        dispatch_payload(
            kind=kind,
            projectId=None,
            datasetId="dataset-1",
            datasetItemIds=["item-1"],
            pageIds=[],
            **overrides,
        )
    )


def manifest_from(request: httpx.Request) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
    )
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_filename() == "manifest.json"
    return json.loads(parts[0].get_content())


@pytest.mark.parametrize("value", [True, "0.12", float("nan"), float("inf"), 10**400])
def test_metric_rejects_nonfinite_or_non_numeric_values(value: Any) -> None:
    with pytest.raises(ValidationError):
        EvaluationMetric(key="cer", label="CER", value=value)


@pytest.mark.parametrize("metadata", [{"": 1}, {"nested": {" ": 1}}, {"nested": {1: 1}}])
def test_report_rejects_invalid_json_keys(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="nonblank strings"):
        report(metadata=metadata)


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": []},
        {"summary": [{"key": "cer", "label": "CER", "value": 1}] * 2},
        {"profileVersion": 0},
        {"tables": [{"key": "table", "title": "Table", "totalRows": -1}]},
        {"samples": [{"id": "sample", "inputId": "item-1"}] * 2},
        {"tables": [{"key": "table", "title": "Table"}] * 2},
        {"metadata": {"score": float("nan")}},
    ],
)
def test_report_rejects_invalid_structure(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        report(**changes)


def test_report_size_limit_and_large_valid_string() -> None:
    report(metadata={"text": "a" * 100_001})
    with pytest.raises(ValidationError, match="10 MiB"):
        report(metadata={"text": "a" * (10 * 1024 * 1024)})


def test_table_row_preserves_values_named_column() -> None:
    row = EvaluationTableRow(values={"values": 0.5, "cer": 0.12})
    assert row.model_dump() == {"values": {"values": 0.5, "cer": 0.12}}


def test_table_row_rejects_unwrapped_cells_instead_of_discarding_them() -> None:
    with pytest.raises(ValidationError):
        EvaluationTableRow.model_validate({"cer": 0.12})


def test_manifest_revalidates_mutated_metric() -> None:
    changed = report()
    changed.summary[0].value = float("nan")
    with pytest.raises(ValidationError, match="finite"):
        ResultBuilder().manifest(evaluation_report=changed)


def test_manifest_rejects_report_with_files() -> None:
    results = ResultBuilder()
    results.add_xml_bytes("item-1", b"<PcGts/>", "page.xml")
    with pytest.raises(ValidationError, match="without pageId or files"):
        results.manifest(evaluation_report=report())


def test_legacy_capability_and_manifest_serialization() -> None:
    assert "evaluationReports" not in ActionCapabilities().model_dump(by_alias=True)
    assert "evaluationReport" not in ResultBuilder().manifest().model_dump(
        by_alias=True,
        exclude_none=True,
    )
    ActionCapabilities.model_json_schema()


@pytest.mark.asyncio
async def test_evaluation_completion_retries_report_only_multipart() -> None:
    manifests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer run-secret"
        manifests.append(manifest_from(request))
        return httpx.Response(503 if len(manifests) == 1 else 200, json={"status": "COMPLETED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        payload = dataset_payload(capabilities={"evaluationReports": True})
        client = ActionClient.from_dispatch(payload, client=http_client, result_retry_backoff=0)
        await ActionContext(payload=payload, client=client).complete_evaluation(report(), "Done")

    assert len(manifests) == 2
    assert manifests[0] == manifests[1]
    assert manifests[0]["files"] == []
    assert manifests[0]["status"] == "completed"
    assert manifests[0]["evaluationReport"]["summary"][0]["value"] == 0.12


@pytest.mark.asyncio
async def test_evaluation_completion_requires_server_capability() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Unsupported reports must not be sent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dataset_payload(), client=http_client)
        with pytest.raises(EvaluationReportsUnsupported):
            await client.complete_evaluation(report())


@pytest.mark.asyncio
async def test_evaluation_completion_honors_cancellation() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.path.endswith("/heartbeat")
        return httpx.Response(200, json={"cancelRequested": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(
            dataset_payload(capabilities={"evaluationReports": True}),
            client=http_client,
        )
        await client.heartbeat()
        with pytest.raises(ActionCancelled):
            await client.complete_evaluation(report())
    assert len(paths) == 2


@pytest.mark.asyncio
async def test_fastapi_preflight_advertises_explicit_report_support() -> None:
    async def process(_ctx: ActionContext) -> None:
        raise AssertionError("preflight must not run the handler")

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
        processor_capabilities={"evaluationReports": True},
    )
    body, headers = signed_preflight()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/preflight", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["capabilities"]["evaluationReports"] is True


@pytest.mark.parametrize("kind", ["TRAINING", "EVALUATION"])
@pytest.mark.asyncio
async def test_fastapi_accepts_signed_dataset_dispatch(kind: str) -> None:
    calls: list[str] = []

    async def process(ctx: ActionContext) -> None:
        assert ctx.payload.dataset_id == "dataset-1"
        assert ctx.payload.project_id is None
        calls.append(ctx.payload.kind)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=process,
    )
    payload = dataset_payload(kind).model_dump(mode="json", by_alias=True)
    body, headers = signed_dispatch(payload=payload)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/dispatch", content=body, headers=headers)
    assert response.status_code == 200
    assert calls == [kind]


@pytest.mark.parametrize("kind", ["TRAINING", "EVALUATION"])
def test_dataset_input_requires_frozen_pair_and_split(kind: str) -> None:
    data = {
        "protocolVersion": 1,
        "runId": "run-1",
        "processorKey": PROCESSOR_ID,
        "kind": kind,
        "datasetId": "dataset-1",
        "pages": [{"id": "item-1", "name": "Page", "sourcePageId": "page-1", "split": "TEST"}],
    }
    with pytest.raises(ValidationError, match="exactly one image and one XML"):
        ActionInput.model_validate(data)


@pytest.mark.parametrize("kind", ["TRAINING", "EVALUATION"])
@pytest.mark.asyncio
async def test_dataset_input_download_and_completion(kind: str) -> None:
    manifests: list[dict[str, Any]] = []
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/input"):
            return httpx.Response(
                200,
                json={
                    "protocolVersion": 1,
                    "runId": "run-1",
                    "processorKey": PROCESSOR_ID,
                    "kind": kind,
                    "datasetId": "dataset-1",
                    "capabilities": {"evaluationReports": kind == "EVALUATION"},
                    "pages": [
                        {
                            "id": "item-1",
                            "name": "Page",
                            "sourcePageId": "page-1",
                            "split": "TRAIN",
                            "images": [
                                {
                                    "id": "image-1",
                                    "fileName": "page.png",
                                    "downloadUrl": "https://larex.example/image",
                                }
                            ],
                            "xml": [
                                {
                                    "id": "xml-1",
                                    "fileName": "page.xml",
                                    "downloadUrl": "https://larex.example/xml",
                                }
                            ],
                        }
                    ],
                },
            )
        if request.url.path in {"/image", "/xml"}:
            return httpx.Response(200, content=b"frozen-input")
        if request.url.path.endswith("/results"):
            manifests.append(manifest_from(request))
            return httpx.Response(200, json={"status": "COMPLETED"})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ActionClient.from_dispatch(dataset_payload(kind), client=http_client)
        inputs = await client.pull_input()
        page = inputs.pages[0]
        assert page.id == "item-1"
        assert page.source_page_id == "page-1"
        assert page.split == "TRAIN"
        assert await client.download_bytes(page.images[0]) == b"frozen-input"
        assert await client.download_bytes(page.xml[0]) == b"frozen-input"
        if kind == "EVALUATION":
            await client.complete_evaluation(
                report(samples=[{"id": "sample-1", "inputId": page.id}])
            )
        else:
            await client.complete(message="Model published")

    assert len(requests) == 4
    assert len(manifests) == 1
    assert manifests[0]["files"] == []
    assert ("evaluationReport" in manifests[0]) == (kind == "EVALUATION")
