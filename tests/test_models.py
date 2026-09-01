import pytest
from pydantic import ValidationError

from larex_actions import InputRequirement, InputRequirements, ParameterChoice


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
