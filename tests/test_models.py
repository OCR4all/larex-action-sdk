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
