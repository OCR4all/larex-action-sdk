from typing import Any

import pytest
from pydantic import ValidationError

from larex_actions import (
    ActionDispatchPayload,
    ActionFile,
    ActionInput,
    InputRequirement,
    InputRequirements,
    ParameterChoice,
)


def test_input_requirement_resolves_target_override() -> None:
    requirement = InputRequirement.model_validate(
        {
            "level": "OPTIONAL",
            "requiredForTargets": ["REGION"],
        }
    )

    assert requirement.level_for("PAGE") == "OPTIONAL"
    assert requirement.level_for("REGION") == "REQUIRED"


def test_input_requirements_parse_machine_payload_shape() -> None:
    requirements = InputRequirements.model_validate(
        {
            "images": {"level": "REQUIRED", "requiredForTargets": []},
            "xml": {"level": "OPTIONAL", "requiredForTargets": ["REGION"]},
        }
    )

    assert requirements.images.level == "REQUIRED"
    assert requirements.xml.required_for_targets == ["REGION"]


def test_parameter_choice_preserves_primitive_type() -> None:
    choice = ParameterChoice.model_validate({"value": 3, "label": "Three"})

    assert choice.value == 3
    assert type(choice.value) is int


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, ["invalid"]])
def test_parameter_choice_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ParameterChoice(value=value, label="Invalid")


def test_legacy_processing_payload_defaults_kind_and_requires_project() -> None:
    payload = ActionDispatchPayload.model_validate(
        {
            "protocolVersion": 1,
            "runId": "run-1",
            "processorId": "processor",
            "workspaceId": "workspace",
            "projectId": "project",
            "pageIds": ["page-1"],
            "secret": "secret",
            "pullUrl": "https://larex.example/input",
            "heartbeatUrl": "https://larex.example/heartbeat",
            "resultUrl": "https://larex.example/results",
        }
    )
    assert payload.kind == "PROCESSING"
    assert payload.project_id == "project"


def test_training_input_parses_dataset_items_and_splits() -> None:
    action_input = ActionInput.model_validate(
        {
            "protocolVersion": 1,
            "runId": "run-2",
            "processorKey": "kraken-layout-training",
            "kind": "TRAINING",
            "datasetId": "dataset-1",
            "pages": [
                {
                    "id": "item-1",
                    "name": "page one",
                    "sourcePageId": "page-1",
                    "split": "TRAIN",
                    "images": [
                        ActionFile(
                            id="image-1",
                            fileName="page.png",
                            downloadUrl="https://larex.example/image",
                        )
                    ],
                    "xml": [
                        ActionFile(
                            id="xml-1", fileName="page.xml", downloadUrl="https://larex.example/xml"
                        )
                    ],
                }
            ],
        }
    )
    assert action_input.project_id is None
    assert action_input.pages[0].id == "item-1"
    assert action_input.pages[0].source_page_id == "page-1"
    assert action_input.pages[0].split == "TRAIN"


def test_training_payload_rejects_project_scope() -> None:
    with pytest.raises(ValidationError):
        ActionDispatchPayload.model_validate(
            {
                "protocolVersion": 1,
                "runId": "run-3",
                "processorId": "processor",
                "workspaceId": "workspace",
                "kind": "TRAINING",
                "projectId": "project",
                "datasetId": "dataset",
                "secret": "secret",
                "pullUrl": "https://larex.example/input",
                "heartbeatUrl": "https://larex.example/heartbeat",
                "resultUrl": "https://larex.example/results",
            }
        )


@pytest.mark.parametrize("kind", ["TRAINING", "EVALUATION"])
@pytest.mark.parametrize("image_count,xml_count", [(1, 1), (2, 1), (0, 1), (2, 0), (2, 2)])
def test_dataset_input_supports_multiple_images_and_requires_one_xml(
    kind: str, image_count: int, xml_count: int
) -> None:
    data = {
        "protocolVersion": 1,
        "runId": "run-multi",
        "inputRequirements": {"images": {"level": "REQUIRED", "multiple": True}},
        "processorKey": "processor",
        "kind": kind,
        "datasetId": "dataset",
        "pages": [
            {
                "id": "item",
                "name": "Page",
                "sourcePageId": "page",
                "split": "TRAIN",
                "images": [
                    {
                        "id": f"image-{index}",
                        "fileName": f"image-{index}.png",
                        "variant": f"variant-{index}",
                        "downloadUrl": f"https://larex.example/{index}",
                    }
                    for index in range(image_count)
                ],
                "xml": [
                    {
                        "id": f"xml-{index}",
                        "fileName": "page.xml",
                        "downloadUrl": f"https://larex.example/xml/{index}",
                    }
                    for index in range(xml_count)
                ],
            }
        ],
    }
    if image_count == 0 or xml_count != 1:
        with pytest.raises(ValidationError, match="at least one image and exactly one XML"):
            ActionInput.model_validate(data)
    else:
        parsed = ActionInput.model_validate(data)
        assert len(parsed.pages) == 1
        assert [image.variant for image in parsed.pages[0].images] == [
            f"variant-{index}" for index in range(image_count)
        ]


@pytest.mark.parametrize("kind", ["PROCESSING", "INTEGRATION", "TRAINING", "EVALUATION"])
@pytest.mark.parametrize("multiple", [None, False, True])
def test_multiple_images_require_explicit_opt_in(kind: str, multiple: bool | None) -> None:
    requirement: dict[str, object] = {"level": "REQUIRED"}
    if multiple is not None:
        requirement["multiple"] = multiple
    data = {
        "protocolVersion": 1,
        "runId": "run",
        "processorKey": "processor",
        "kind": kind,
        "projectId": "project" if kind in {"PROCESSING", "INTEGRATION"} else None,
        "datasetId": "dataset" if kind in {"TRAINING", "EVALUATION"} else None,
        "inputRequirements": {"images": requirement, "xml": {"level": "REQUIRED"}},
        "pages": [
            {
                "id": "item",
                "name": "Page",
                "sourcePageId": "page",
                "split": "TRAIN",
                "images": [
                    {
                        "id": f"image-{index}",
                        "fileName": f"image-{index}.png",
                        "downloadUrl": f"https://larex.example/image/{index}",
                    }
                    for index in range(2)
                ],
                "xml": [
                    {
                        "id": "xml",
                        "fileName": "page.xml",
                        "downloadUrl": "https://larex.example/xml",
                    }
                ],
            }
        ],
    }
    if multiple:
        assert len(ActionInput.model_validate(data).pages[0].images) == 2
    else:
        with pytest.raises(ValidationError, match="Multiple image variants require"):
            ActionInput.model_validate(data)


def test_image_types_are_additive_and_use_wire_aliases() -> None:
    requirement = InputRequirement.model_validate({"level": "REQUIRED", "imageTypes": ["BINARY"]})
    assert requirement.image_types == ["BINARY"]
    assert requirement.model_dump(by_alias=True)["imageTypes"] == ["BINARY"]
    image = ActionFile.model_validate(
        {
            "id": "i",
            "fileName": "page.png",
            "downloadUrl": "https://example.org/i",
            "imageType": "BINARY",
        }
    )
    assert image.image_type == "BINARY"
    assert "imageTypes" not in InputRequirement().model_dump(by_alias=True)
    assert (
        ActionFile.model_validate(
            {"id": "i", "fileName": "page.png", "downloadUrl": "https://example.org/i"}
        ).image_type
        is None
    )


@pytest.mark.parametrize("image_type", ["COLOR", "GRAYSCALE", "UNKNOWN", None])
def test_input_rejects_images_outside_declared_types(image_type: str | None) -> None:
    with pytest.raises(ValidationError, match="imageTypes"):
        ActionInput.model_validate(
            {
                "protocolVersion": 1,
                "runId": "run",
                "processorKey": "processor",
                "projectId": "project",
                "inputRequirements": {"images": {"level": "REQUIRED", "imageTypes": ["BINARY"]}},
                "pages": [
                    {
                        "id": "p",
                        "name": "p",
                        "images": [
                            {
                                "id": "i",
                                "fileName": "i.png",
                                "downloadUrl": "https://example.org/i",
                                "imageType": image_type,
                            }
                        ],
                    }
                ],
            }
        )


def test_input_accepts_binary_images_and_legacy_unrestricted_metadata() -> None:
    payload: dict[str, Any] = {
        "protocolVersion": 1,
        "runId": "run",
        "processorKey": "processor",
        "projectId": "project",
        "inputRequirements": {"images": {"level": "REQUIRED", "imageTypes": ["BINARY"]}},
        "pages": [
            {
                "id": "p",
                "name": "p",
                "images": [
                    {
                        "id": "i",
                        "fileName": "i.png",
                        "downloadUrl": "https://example.org/i",
                        "imageType": "BINARY",
                    }
                ],
            }
        ],
    }
    assert ActionInput.model_validate(payload).pages[0].images[0].image_type == "BINARY"
    payload.pop("inputRequirements")
    payload["pages"][0]["images"][0].pop("imageType")
    assert ActionInput.model_validate(payload).pages[0].images[0].image_type is None
