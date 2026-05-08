from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

RunStatus = Literal["running", "failed"]
ResultStatus = Literal["completed", "failed"]
FileType = Literal["image", "xml"]
PROTOCOL_VERSION = 1


class LarexModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ActionDispatchPayload(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    run_id: str = Field(alias="runId")
    processor_id: str = Field(alias="processorId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    page_ids: list[str] = Field(alias="pageIds", default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    secret: SecretStr
    pull_url: str = Field(alias="pullUrl")
    heartbeat_url: str = Field(alias="heartbeatUrl")
    result_url: str = Field(alias="resultUrl")


class ActionFile(LarexModel):
    id: str
    file_name: str = Field(alias="fileName")
    variant: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    file_size: int | None = Field(default=None, alias="fileSize")
    download_url: str = Field(alias="downloadUrl")


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
    cancel_requested: bool = Field(default=False, alias="cancelRequested")


class HeartbeatResponse(LarexModel):
    cancel_requested: bool = Field(default=False, alias="cancelRequested")


class ResultFile(LarexModel):
    field_name: str = Field(alias="fieldName")
    page_id: str = Field(alias="pageId")
    type: FileType
    variant: str | None = None
    file_name: str = Field(alias="fileName")


class ResultManifest(LarexModel):
    protocol_version: Literal[1] = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    status: ResultStatus = "completed"
    message: str | None = None
    files: list[ResultFile] = Field(default_factory=list)
