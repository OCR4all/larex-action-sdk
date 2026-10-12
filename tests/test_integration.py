from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from larex_actions import (
    ActionClient,
    ActionContext,
    ActionDispatchPayload,
    ActionInput,
    ResultBuilder,
)
from larex_actions.fastapi import create_larex_action_app

from .conftest import PROCESSOR_ID, SECRET, dispatch_payload, signed_dispatch


def payload(dataset: bool = False) -> ActionDispatchPayload:
    return ActionDispatchPayload.model_validate(
        dispatch_payload(
            kind="INTEGRATION",
            projectId=None if dataset else "project",
            datasetId="dataset" if dataset else None,
            pageIds=[] if dataset else ["page"],
            datasetItemIds=["item"] if dataset else [],
        )
    )


def input_data(dataset: bool = False, files: str = "xml") -> dict[str, Any]:
    return {
        "protocolVersion": 1,
        "runId": "run-1",
        "processorKey": PROCESSOR_ID,
        "kind": "INTEGRATION",
        "projectId": None if dataset else "project",
        "datasetId": "dataset" if dataset else None,
        "inputRequirements": {
            "images": {"level": "OPTIONAL" if files in {"images", "both"} else "NONE"},
            "xml": {"level": "OPTIONAL" if files in {"xml", "both"} else "NONE"},
        },
        "pages": [
            {
                "id": "item" if dataset else "page",
                "name": "Page",
                "sourcePageId": "source-page",
                "images": [
                    {
                        "id": "image",
                        "fileName": "image.png",
                        "downloadUrl": "https://larex.example/image",
                    }
                ]
                if files in {"images", "both"}
                else [],
                "xml": [
                    {
                        "id": "xml",
                        "fileName": "page.xml",
                        "downloadUrl": "https://larex.example/xml",
                    }
                ]
                if files in {"xml", "both"}
                else [],
            }
        ],
    }


@pytest.mark.parametrize("dataset", [False, True])
@pytest.mark.parametrize("files", ["images", "xml", "both"])
def test_integration_accepts_both_scopes_and_declared_files(dataset: bool, files: str) -> None:
    assert payload(dataset).kind == "INTEGRATION"
    parsed = ActionInput.model_validate(input_data(dataset, files))
    assert parsed.pages[0].split is None
    assert parsed.kind == "INTEGRATION"


@pytest.mark.parametrize("scope", [{}, {"projectId": "project", "datasetId": "dataset"}])
def test_integration_rejects_ambiguous_scope(scope: dict[str, str]) -> None:
    data = dispatch_payload(kind="INTEGRATION", projectId=None, datasetId=None)
    data.update(scope)
    with pytest.raises(ValidationError, match="exactly one"):
        ActionDispatchPayload.model_validate(data)


def test_integration_rejects_empty_duplicate_and_undeclared_inputs() -> None:
    data = input_data()
    data["pages"][0]["xml"] = []
    with pytest.raises(ValidationError, match="at least one file"):
        ActionInput.model_validate(data)
    data = input_data()
    data["pages"].append(data["pages"][0])
    with pytest.raises(ValidationError, match="unique"):
        ActionInput.model_validate(data)
    data = input_data()
    data["inputRequirements"]["images"]["level"] = "REQUIRED"
    with pytest.raises(ValidationError, match="required images"):
        ActionInput.model_validate(data)
    data = input_data()
    data["inputRequirements"]["xml"]["level"] = "NONE"
    with pytest.raises(ValidationError, match="undeclared xml"):
        ActionInput.model_validate(data)


@pytest.mark.parametrize(
    "capability", ["incrementalPageResults", "customFileResults", "evaluationReports"]
)
def test_integration_rejects_managed_result_capabilities(capability: str) -> None:
    data = payload().model_dump(by_alias=True)
    data["capabilities"] = {capability: True}
    with pytest.raises(ValidationError, match="managed result"):
        ActionDispatchPayload.model_validate(data)


@pytest.mark.asyncio
async def test_integration_completes_with_message_and_rejects_files_before_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "COMPLETED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ActionClient.from_dispatch(payload(), client=http)
        results = ResultBuilder()
        results.add_xml_bytes("page", b"<xml/>", "page.xml")
        with pytest.raises(ValueError, match="only final status"):
            await client.complete(results)
        assert not requests
        await client.complete(message="Exported 1 page")
    assert len(requests) == 1
    assert b"Exported 1 page" in requests[0].content
    assert b'"files":[]' in requests[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", [False, True])
async def test_fastapi_accepts_signed_integration_dispatch(dataset: bool) -> None:
    calls: list[str] = []

    async def integrate(ctx: ActionContext) -> None:
        calls.append(ctx.payload.kind)

    app = create_larex_action_app(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        handler=integrate,
        processor_capabilities={"incrementalPageResults": False, "customFileResults": False},
    )
    data = payload(dataset).model_dump(mode="json", by_alias=True)
    data["secret"] = "run-secret"
    body, headers = signed_dispatch(payload=data)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post("/dispatch", content=body, headers=headers)
    assert response.status_code == 200
    assert calls == ["INTEGRATION"]
