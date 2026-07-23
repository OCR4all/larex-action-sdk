from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

RunStatus = Literal["running", "failed", "cancelled"]
ResultStatus = Literal["running", "completed", "failed"]
FileType = Literal["image", "xml", "file"]
ActionTarget = Literal["PAGE", "REGION", "TEXT_LINE"]
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


class ActionCapabilities(LarexModel):
    incremental_page_results: bool = Field(default=False, alias="incrementalPageResults")
    custom_file_results: bool = Field(default=False, alias="customFileResults")


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


class ActionDispatchPayload(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    run_id: str = Field(alias="runId")
    processor_id: str = Field(alias="processorId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    page_ids: list[str] = Field(alias="pageIds", default_factory=list)
    target_selection: ActionTargetSelection | None = Field(default=None, alias="targetSelection")
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
