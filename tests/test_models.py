from larex_actions import InputRequirement, InputRequirements


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
