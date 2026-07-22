from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, Discriminator
from typing import Any


class CoreStartedEvent(BaseModel):
    type: Literal["core.started"] = "core.started"
    listen_addr: str
    version: str


class RunStartedEvent(BaseModel):
    type: Literal["run.started"] = "run.started"
    runs_id: str
    goal: str
    ts: str


class RunFinishedEvent(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    runs_id: str
    status: str  # "success" / "failed"
    reason: str | None = None  # "exceeded_max_steps" / "cancelled" / "llm_error"
    steps: int
    ts: str


class StepStartedEvent(BaseModel):
    type: Literal["step.stated"] = "step.started"
    runs_id: str
    step: int
    ts: str


class StepFinishedEvent(BaseModel):
    type: Literal["step.stated"] = "step.finished"
    runs_id: str
    step: int
    ts: str


class ToolCallStartedEvent(BaseModel):
    type: Literal["tool.call_started"] = "tool.call_started"
    runs_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    ts: str


class ToolCallFinishedEvent(BaseModel):
    type: Literal["tool.call_finished"] = "tool.call_finished"
    runs_id: str
    tool_use_id: str
    tool_name: str
    elapsed_ms: int
    ts: str


class ToolCallFailedEvent(BaseModel):
    type: Literal["tool.call_failed"] = "tool.call_failed"
    runs_id: str
    tool_use_id: str
    tool_name: str
    error_type: str  # "runtime_error" | "timeout" | "schema_error"
    error_message: str
    elapsed_ms: int
    ts: str


class LlmTokenEvent(BaseModel):
    type: Literal["llm.token"] = "llm.token"
    runs_id: str
    token: str
    ts: str


class LlmUsageEvent(BaseModel):
    type: Literal["llm.usage"] = "llm.usage"
    runs_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    ts: str


class LlmModelSelectedEvent(BaseModel):
    type: Literal["llm.model_selected"] = "llm.model_selected"
    runs_id: str
    model: str
    strategy: str  # "static" | "rule_based" | "cost_budget"
    ts: str


class LogLineEvent(BaseModel):
    type: Literal["log.line"] = "log.line"
    runs_id: str
    level: str  # "DEBUG" | "INFO" | "WARNING" | "ERROR"
    source: str
    message: str
    ts: str


Event = Annotated[
    CoreStartedEvent
    | RunStartedEvent
    | RunFinishedEvent
    | StepStartedEvent
    | StepFinishedEvent
    | ToolCallStartedEvent
    | ToolCallFinishedEvent
    | ToolCallFailedEvent
    | LlmTokenEvent
    | LlmUsageEvent
    | LlmModelSelectedEvent
    | LogLineEvent,
    Discriminator("type"),
]
