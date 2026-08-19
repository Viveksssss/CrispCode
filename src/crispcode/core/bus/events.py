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
    output: str = ""  # tool result content,for TUI display


class ToolCallFailedEvent(BaseModel):
    type: Literal["tool.call_failed"] = "tool.call_failed"
    runs_id: str
    tool_use_id: str
    tool_name: str
    error_class: str  # "runtime_error" | "timeout" | "schema_error"
    error_message: str
    elapsed_ms: int
    ts: str
    attempt: int = 1


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
    context_pct: float = 0.0
    ts: str


class LlmThinkingEvent(BaseModel):
    type: Literal["llm.thinking"] = "llm.thinking"
    runs_id: str
    thinking: str  # thinking 文本
    signature: str = ""  # 可选，Anthropic 的 signature
    step: int = 0
    ts: str


class LlmThinkingTokenEvent(BaseModel):
    """逐 token 推送 thinking 内容（与 LlmTokenEvent 对称，用于流式显示）"""
    type: Literal["llm.thinking.token"] = "llm.thinking.token"
    runs_id: str
    token: str
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


class SessionCreatedEvent(BaseModel):
    type: Literal["session.created"] = "session.created"
    session_id: str
    mode: str
    ts: str


class SessionMessageReceivedEvent(BaseModel):
    type: Literal["session.message_received"] = "session.message_received"
    session_id: str
    content: str
    ts: str


class SessionIdleEvent(BaseModel):
    type: Literal["session.idle"] = "session.idle"
    session_id: str
    last_runs_id: str
    ts: str


class SessionResumedEvent(BaseModel):
    type: Literal["session.resumed"] = "session.resumed"
    session_id: str
    ts: str


class SessionClosedEvent(BaseModel):
    type: Literal["session.closed"] = "session.closed"
    session_id: str
    ts: str


class PermissionRequestedEvent(BaseModel):
    type: Literal["permission.requested"] = "permission.requested"
    runs_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    param_preview: str
    session_id: str
    ts: str


class PermissionGrantedEvent(BaseModel):
    type: Literal["permission.granted"] = "permission.granted"
    runs_id: str
    tool_use_id: str
    decision: str  # "allow_once" | "always_allow" | "auto_allow"
    ts: str


class PermissionDeniedEvent(BaseModel):
    type: Literal["permission.denied"] = "permission.denied"
    runs_id: str
    tool_use_id: str
    decision: str
    ts: str


class ContextCompactedEvent(BaseModel):
    type: Literal["context.compacted"] = "context.compacted"
    session_id: str
    runs_id: str
    original_tokens: int
    summary_tokens: int
    ts: str


class SubagentStartedEvent(BaseModel):
    type: Literal["subagent.started"] = "subagent.started"
    runs_id: str
    parent_runs_id: str
    description: str
    ts: str


class SubagentFinishedEvent(BaseModel):
    type: Literal["subagent.finished"] = "subagent.finished"
    runs_id: str
    parent_runs_id: str
    status: str
    ts: str


class SkillInvokeEvent(BaseModel):
    type: Literal["skill.invoked"] = "skill.invoked"
    skill_name: str
    arguments: str
    runs_id: str
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
    | LlmThinkingEvent
    | LlmThinkingTokenEvent
    | LogLineEvent
    | SessionCreatedEvent
    | SessionMessageReceivedEvent
    | SessionIdleEvent
    | SessionResumedEvent
    | SessionClosedEvent
    | PermissionRequestedEvent
    | PermissionGrantedEvent
    | PermissionDeniedEvent
    | ContextCompactedEvent
    | SubagentStartedEvent
    | SubagentFinishedEvent
    | SkillInvokeEvent,
    Discriminator("type"),
]
