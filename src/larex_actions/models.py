from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

RunStatus = Literal["running", "failed", "cancelled"]
ResultStatus = Literal["running", "completed", "failed"]
FileType = Literal["image", "xml", "file"]
ActionTarget = Literal["PAGE", "REGION", "TEXT_LINE"]
InputLevel = Literal["NONE", "OPTIONAL", "REQUIRED"]
PROTOCOL_VERSION = 1


class LarexModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TargetSelectionPage(LarexModel):
    page_id: str = Field(alias="pageId")
    region_ids: list[str] = Field(default_factory=list, alias="regionIds")
    text_line_ids: list[str] = Field(default_factory=list, alias="textLineIds")


class ActionTargetSelection(LarexModel):
    type: ActionTarget = "PAGE"
    pages: list[TargetSelectionPage] = Field(default_factory=list)


class InputRequirement(LarexModel):
    level: InputLevel = "NONE"
    required_for_targets: list[ActionTarget] = Field(
        default_factory=list, alias="requiredForTargets"
    )

    def level_for(self, target: ActionTarget) -> InputLevel:
        return "REQUIRED" if target in self.required_for_targets else self.level


class InputRequirements(LarexModel):
    images: InputRequirement = Field(default_factory=InputRequirement)
    xml: InputRequirement = Field(default_factory=InputRequirement)


class ActionCapabilities(LarexModel):
    incremental_page_results: bool = Field(default=False, alias="incrementalPageResults")
    custom_file_results: bool = Field(default=False, alias="customFileResults")
    parameter_value_discovery: bool = Field(default=False, alias="parameterValueDiscovery")


class PreflightRequest(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    processor_id: str = Field(alias="processorId")
    capabilities: ActionCapabilities = Field(default_factory=ActionCapabilities)


class PreflightResponse(LarexModel):
    status: Literal["ok"] = "ok"
    protocol_version: Literal[1] = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    processor_id: str = Field(alias="processorId")
    capabilities: ActionCapabilities


class ParameterChoice(LarexModel):
    value: Any
    label: str

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: Any) -> Any:
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("value must be a string, number, integer, or boolean")
        if isinstance(value, str) and len(value) > 1_024:
            raise ValueError("string values must not exceed 1024 characters")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("numeric values must be finite")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        if len(value) > 256:
            raise ValueError("label must not exceed 256 characters")
        return value


class ParameterValuesRequest(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    processor_id: str = Field(alias="processorId")
    providers: list[str]

    @model_validator(mode="after")
    def validate_providers(self) -> ParameterValuesRequest:
        if not self.providers:
            raise ValueError("providers must not be empty")
        if len(self.providers) > 100:
            raise ValueError("providers must not contain more than 100 entries")
        if any(not provider or len(provider) > 64 for provider in self.providers):
            raise ValueError("provider names must contain 1 to 64 characters")
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("providers must not contain duplicates")
        return self


class ParameterValuesResponse(LarexModel):
    status: Literal["ok"] = "ok"
    protocol_version: Literal[1] = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    processor_id: str = Field(alias="processorId")
    values: dict[str, list[ParameterChoice]]


class ActionDispatchPayload(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    run_id: str = Field(alias="runId")
    processor_id: str = Field(alias="processorId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    page_ids: list[str] = Field(alias="pageIds", default_factory=list)
    target_selection: ActionTargetSelection | None = Field(default=None, alias="targetSelection")
    input_requirements: InputRequirements = Field(
        default_factory=InputRequirements, alias="inputRequirements"
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    secret: SecretStr
    pull_url: str = Field(alias="pullUrl")
    heartbeat_url: str = Field(alias="heartbeatUrl")
    result_url: str = Field(alias="resultUrl")
    capabilities: ActionCapabilities = Field(default_factory=ActionCapabilities)

    @property
    def target(self) -> ActionTargetSelection | None:
        return self.target_selection


class ActionFile(LarexModel):
    id: str
    file_name: str = Field(alias="fileName")
    variant: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    file_size: int | None = Field(default=None, alias="fileSize")
    download_url: str = Field(alias="downloadUrl")


ActionInputTargetPage = TargetSelectionPage
ActionInputTargetSelection = ActionTargetSelection


class ActionPage(LarexModel):
    id: str
    name: str
    images: list[ActionFile] = Field(default_factory=list)
    xml: list[ActionFile] = Field(default_factory=list)


class ActionInput(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    run_id: str = Field(alias="runId")
    processor_key: str = Field(alias="processorKey")
    project_id: str = Field(alias="projectId")
    parameters: dict[str, Any] = Field(default_factory=dict)
    pages: list[ActionPage] = Field(default_factory=list)
    target_selection: ActionInputTargetSelection | None = Field(
        default=None, alias="targetSelection"
    )
    input_requirements: InputRequirements = Field(
        default_factory=InputRequirements, alias="inputRequirements"
    )
    capabilities: ActionCapabilities = Field(default_factory=ActionCapabilities)
    cancel_requested: bool = Field(default=False, alias="cancelRequested")

    @property
    def target(self) -> ActionInputTargetSelection | None:
        return self.target_selection


class HeartbeatResponse(LarexModel):
    cancel_requested: bool = Field(default=False, alias="cancelRequested")


class ResultFile(LarexModel):
    field_name: str = Field(alias="fieldName")
    page_id: str | None = Field(default=None, alias="pageId")
    type: FileType
    variant: str | None = None
    file_name: str = Field(alias="fileName")


class ResultManifest(LarexModel):
    protocol_version: Literal[1] = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    status: ResultStatus = "completed"
    message: str | None = None
    page_id: str | None = Field(default=None, alias="pageId")
    files: list[ResultFile] = Field(default_factory=list)
