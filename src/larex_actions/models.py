from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

RunStatus = Literal["running", "failed", "cancelled"]
ResultStatus = Literal["running", "completed", "failed"]
FileType = Literal["image", "xml", "file"]
ActionTarget = Literal["PAGE", "REGION", "TEXT_LINE"]
InputLevel = Literal["NONE", "OPTIONAL", "REQUIRED"]
ActionKind = Literal["PROCESSING", "TRAINING", "EVALUATION"]
DatasetSplit = Literal["TRAIN", "VAL", "TEST"]
MetricFormat = Literal["NUMBER", "INTEGER", "PERCENT"]
MetricDirection = Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER", "NEUTRAL"]
TableColumnType = Literal["STRING", "NUMBER", "INTEGER", "BOOLEAN"]
EvaluationSampleStatus = Literal["OK", "SKIPPED", "FAILED"]
PROTOCOL_VERSION = 1
MAX_EVALUATION_REPORT_BYTES = 10 * 1024 * 1024


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
    # Omit the additive capability when false so legacy preflight responses keep
    # their exact wire shape; processors that support reports advertise `true`.
    evaluation_reports: bool = Field(
        default=False,
        alias="evaluationReports",
        exclude_if=lambda value: value is False,
    )


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
    kind: ActionKind = "PROCESSING"
    project_id: str | None = Field(default=None, alias="projectId")
    dataset_id: str | None = Field(default=None, alias="datasetId")
    page_ids: list[str] = Field(alias="pageIds", default_factory=list)
    dataset_item_ids: list[str] = Field(alias="datasetItemIds", default_factory=list)
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

    @model_validator(mode="after")
    def validate_resource_scope(self) -> ActionDispatchPayload:
        if self.kind in {"TRAINING", "EVALUATION"}:
            if not self.dataset_id or self.project_id:
                raise ValueError(f"{self.kind} dispatches require datasetId and no projectId")
            if not self.dataset_item_ids:
                raise ValueError(f"{self.kind} dispatches require datasetItemIds")
            if len(self.dataset_item_ids) != len(set(self.dataset_item_ids)):
                raise ValueError("datasetItemIds must not contain duplicates")
        elif not self.project_id or self.dataset_id:
            raise ValueError("PROCESSING dispatches require projectId and no datasetId")
        return self

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
    source_page_id: str | None = Field(default=None, alias="sourcePageId")
    split: DatasetSplit | None = None
    images: list[ActionFile] = Field(default_factory=list)
    xml: list[ActionFile] = Field(default_factory=list)


class ActionInput(LarexModel):
    protocol_version: Literal[1] = Field(alias="protocolVersion")
    run_id: str = Field(alias="runId")
    processor_key: str = Field(alias="processorKey")
    kind: ActionKind = "PROCESSING"
    project_id: str | None = Field(default=None, alias="projectId")
    dataset_id: str | None = Field(default=None, alias="datasetId")
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

    @model_validator(mode="after")
    def validate_resource_scope(self) -> ActionInput:
        if self.kind in {"TRAINING", "EVALUATION"}:
            if not self.dataset_id or self.project_id:
                raise ValueError(f"{self.kind} inputs require datasetId and no projectId")
            seen_ids: set[str] = set()
            for page in self.pages:
                if page.id in seen_ids:
                    raise ValueError("dataset Action inputs must not contain duplicate item IDs")
                seen_ids.add(page.id)
                if not page.source_page_id or page.split is None:
                    raise ValueError("dataset Action inputs require sourcePageId and split")
                # Dataset actions always receive one frozen image/XML pair.  This is
                # independent of the generic processing input requirement fields;
                # those fields describe project-scoped actions only.
                if len(page.images) != 1 or len(page.xml) != 1:
                    raise ValueError(
                        "dataset Action inputs require exactly one image and one XML file per item"
                    )
        elif not self.project_id or self.dataset_id:
            raise ValueError("PROCESSING inputs require projectId and no datasetId")
        return self

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


class EvaluationMetric(LarexModel):
    key: str
    label: str
    value: StrictInt | StrictFloat
    format: MetricFormat = "NUMBER"
    unit: str | None = None
    direction: MetricDirection = "NEUTRAL"

    @field_validator("key", "label")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("metric text must not be blank")
        if len(value) > 256:
            raise ValueError("metric text must not exceed 256 characters")
        return value

    @field_validator("value")
    @classmethod
    def validate_finite(cls, value: StrictInt | StrictFloat) -> StrictInt | StrictFloat:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("metric value must be finite")
        return value


class EvaluationTableColumn(LarexModel):
    key: str
    label: str
    type: TableColumnType = "STRING"

    @field_validator("key", "label")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("table column text must not be blank")
        if len(value) > 256:
            raise ValueError("table column text must not exceed 256 characters")
        return value


def _validate_json_value(value: Any, *, field_name: str = "value") -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, (int, float)):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{field_name} must contain finite numbers")
        return value
    if isinstance(value, list):
        return [_validate_json_value(item, field_name=field_name) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not key.strip() for key in value):
            raise ValueError(f"{field_name} object keys must be nonblank strings")
        return {
            key: _validate_json_value(item, field_name=field_name) for key, item in value.items()
        }
    raise ValueError(f"{field_name} must contain JSON values")


class EvaluationTableRow(LarexModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_value(value, field_name="table row")


class EvaluationTable(LarexModel):
    key: str
    title: str
    columns: list[EvaluationTableColumn] = Field(default_factory=list)
    rows: list[EvaluationTableRow] = Field(default_factory=list)
    truncated: bool = False
    total_rows: int = Field(default=0, alias="totalRows")

    @model_validator(mode="after")
    def validate_table(self) -> EvaluationTable:
        if not self.key.strip() or not self.title.strip():
            raise ValueError("evaluation table key and title must not be blank")
        if self.total_rows == 0 and self.rows:
            self.total_rows = len(self.rows)
        if self.total_rows < len(self.rows):
            raise ValueError("totalRows must be greater than or equal to rows length")
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("evaluation table columns must have unique keys")
        return self


class EvaluationSample(LarexModel):
    id: str
    input_id: str = Field(alias="inputId")
    target_id: str | None = Field(default=None, alias="targetId")
    label: str | None = None
    status: EvaluationSampleStatus = "OK"
    fields: dict[str, Any] = Field(default_factory=dict)
    metrics: list[EvaluationMetric] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_value(value, field_name="sample fields")

    @field_validator("id", "input_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sample IDs must not be blank")
        return value


class EvaluationReport(LarexModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    profile: str
    profile_version: int = Field(alias="profileVersion")
    title: str
    summary: list[EvaluationMetric] = Field(default_factory=list)
    tables: list[EvaluationTable] = Field(default_factory=list)
    samples: list[EvaluationSample] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_report(self) -> EvaluationReport:
        if not self.profile.strip() or not self.title.strip():
            raise ValueError("evaluation profile and title must not be blank")
        if self.profile_version < 1:
            raise ValueError("profileVersion must be positive")
        summary_keys = [metric.key for metric in self.summary]
        if len(summary_keys) != len(set(summary_keys)):
            raise ValueError("summary metric keys must be unique")
        table_keys = [table.key for table in self.tables]
        if len(table_keys) != len(set(table_keys)):
            raise ValueError("evaluation table keys must be unique")
        sample_ids = [sample.id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("evaluation sample IDs must be unique")
        _validate_json_value(self.metadata, field_name="metadata")
        if not self.summary:
            raise ValueError("evaluation reports require at least one summary metric")
        try:
            size = len(self.model_dump_json(by_alias=True).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation report must be serializable JSON") from exc
        if size > MAX_EVALUATION_REPORT_BYTES:
            raise ValueError("evaluation report must not exceed 10 MiB")
        return self


class ResultManifest(LarexModel):
    protocol_version: Literal[1] = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    status: ResultStatus = "completed"
    message: str | None = None
    page_id: str | None = Field(default=None, alias="pageId")
    files: list[ResultFile] = Field(default_factory=list)
    evaluation_report: EvaluationReport | None = Field(default=None, alias="evaluationReport")

    @field_validator("evaluation_report", mode="before")
    @classmethod
    def revalidate_report(cls, value: Any) -> Any:
        # Reports are mutable while processors assemble them. Validate nested
        # data again before serialization, including changes made after creation.
        if isinstance(value, EvaluationReport):
            return value.model_dump(by_alias=True)
        return value

    @model_validator(mode="after")
    def validate_evaluation_result(self) -> ResultManifest:
        if self.evaluation_report is not None and (
            self.status == "running" or self.page_id is not None or self.files
        ):
            raise ValueError("evaluation reports require terminal results without pageId or files")
        return self
