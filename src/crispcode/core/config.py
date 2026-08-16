from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7437
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_FILE = "~/.crispcode/logs/core.log"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_CONFIG_PATH = "~/.crispcode/config.toml"
_DEFAULT_MAX_STEPS = 20
_DEFAULT_MODEL = "deepseek-v4-flash"
_DEFAULT_TRACE_FILE = "~/.crispcode/trace/daemon.log"


@dataclass
class LoggingConfig:
    level: str = _DEFAULT_LOG_LEVEL
    file: str = _DEFAULT_LOG_FILE
    format: str = _DEFAULT_LOG_FORMAT  # "text" / "json"


@dataclass
class AgentConfig:
    max_steps: int = _DEFAULT_MAX_STEPS


@dataclass
class TraceConfig:
    enabled: bool = False
    file: str = "~/.crispcode/logs/trace.log"
    include_llm_payload: bool = False


@dataclass
class BaseConfig:
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_BASE_URL: str | None = None
    CRISP_LLM_DEFAULT_MODEL: str | None = None


@dataclass
class LlmConfig:
    default_model: str = _DEFAULT_MODEL
    router: str = "static"  # / "static" / "rule_based" (S4) / cost_budget (S6)


@dataclass
class TuiConfig:
    tokens_enabled: bool = True


@dataclass
class PermissionConfig:
    timeout_s: float = 60.0  # 审批超时秒数；0 表示不超时


@dataclass
class CompactionConfig:
    auto_threshold: float = (
        0.0  # context_pct 触发自动压缩的阈值（0 表示禁用，推荐用手动 /compact）
    )
    tool_result_limit: int = 8_000  # tool_result 截断触发字符数
    tool_result_keep: int = 4_000  # 截断后保留的前缀字符数


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"


@dataclass
class CrispConfig:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    config: BaseConfig = field(default_factory=BaseConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    tui_config: TuiConfig = field(default_factory=TuiConfig)
    permission: PermissionConfig = field(default_factory=PermissionConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)


def get_config() -> CrispConfig:
    # 1. 创建默认配置实例
    config = CrispConfig()

    # 2. 加载 .env 文件（不覆盖已有系统环境变量）
    load_dotenv(".env", override=False)

    # 3. 确定配置文件路径（环境变量 or 默认）
    config_path = Path(
        os.environ.get("CRISP_CONFIG", _DEFAULT_CONFIG_PATH)
    ).expanduser()

    # 4. 如果配置文件存在，解析并应用
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"Config parse error({config_path}):{e}")
        _apply_toml(config, data)

    # 5. 应用环境变量（最高优先级）
    _apply_env(config)
    return config


def _apply_toml(config: CrispConfig, data: dict[str, Any]) -> None:
    # print(f"DEBUG: data = {data}")  # 添加这行
    unknown = set(data.keys()) - {
        "config",
        "core",
        "logging",
        "trace",
        "agent",
        "llm",
        "tui",
        "permission",
        "compaction",
    }
    if unknown:
        raise SystemExit(
            f"Unknown top-level config keys:{', '.join(sorted(unknown))}\nNow only support core,logging,trace"
        )
    if "config" in data:
        config_data = data["config"]
        if not isinstance(config_data, dict):
            raise SystemExit("Config error: [config] must be a table")
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CRISP_LLM_DEFAULT_MODEL",
        ):
            if key in config_data:
                val = config_data[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: config.{key} must be a string")
                setattr(config.config, key, val)
                os.environ[key] = val  # 设置环境变量

    if "core" in data:
        core = data["core"]
        if not isinstance(core, dict):
            raise SystemExit("Config error: [core] must be a table")
        if "host" in core:
            val = core["host"]
            if not isinstance(val, str):
                raise SystemExit("Config error: core.host must be a string")
            config.host = val
        if "port" in core:
            val = core["port"]
            if not isinstance(val, int):
                raise SystemExit("Config error: core.port must be an int")
            config.port = val
    if "logging" in data:
        log = data["logging"]
        if not isinstance(log, dict):
            raise SystemExit("Config error: [logging] must be a table")
        unknown_log: set[str] = set(log.keys()) - {"level", "file", "format"}
        if unknown_log:
            raise SystemExit(
                f"Unknown [logging] keys :{', '.join(sorted(unknown_log))}"
            )
        for key in ("level", "file", "format"):
            if key in log:
                val = log[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: logging.{key} must be a string")
                setattr(config.logging, key, val)

    if "agent" in data:
        agent = data["agent"]
        if not isinstance(agent, dict):
            raise SystemExit("Config error: [agent] must be a table")
        unknown_agent: set[str] = set(agent.keys()) - {"max_steps"}
        if unknown_agent:
            raise SystemExit(
                f"Unknown [agent] keys: {', '.join(sorted(unknown_agent))}"
            )
        if "max_steps" in agent:
            val = agent["max_steps"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit(
                    "Config error: agent.max_steps must be a positive integer"
                )
            config.agent.max_steps = val

    if "llm" in data:
        llm = data["llm"]
        if not isinstance(llm, dict):
            raise SystemExit("Config error: [llm] must be a table")
        unknown_llm: set[str] = set(llm.keys()) - {"default_model", "router"}
        if unknown_llm:
            raise SystemExit(f"Unknown [llm] keys: {', '.join(sorted(unknown_llm))}")
        if "default_model" in llm:
            val = llm["default_model"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.default_model must be a string")
            config.llm.default_model = val
        if "router" in llm:
            val = llm["router"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.router must be a string")
            config.llm.router = val

    if "trace" in data:
        trace = data["trace"]
        if not isinstance(trace, dict):
            raise SystemExit("Config error: [trace] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {
            "enabled",
            "file",
            "include_llm_payload",
        }
        if unknown_trace:
            raise SystemExit(
                f"Unknown [trace] keys :{', '.join(sorted(unknown_trace))}"
            )
        for key in ("enabled", "file", "include_llm_payload"):
            if key in trace:
                val = trace[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: trace.{key} must be a string")
                setattr(config.trace, key, val)
    if "tui" in data:
        tui = data["tui"]
        if not isinstance(tui, dict):
            raise SystemExit("Config error: [tui] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {
            "tokens_enabled",
        }
        for key in ["tokens_enabled"]:
            if key in tui:
                val = tui[key]
                if isinstance(val, bool):
                    config.tui_config.tokens_enabled = val
                elif isinstance(val, str):
                    config.tui_config.tokens_enabled = val.lower() not in (
                        "0",
                        "false",
                        "no",
                    )
                else:
                    raise SystemExit(
                        f"Config error: tui.tokens_enabled must be a boolean or string"
                    )

    if "permission" in data:
        permission = data["permission"]
        if not isinstance(permission, dict):
            raise SystemExit("Config error: [permission] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {
            "timeout_s",
        }
        for key in ["timeout_s"]:
            if key in permission:
                val = permission[key]
                if isinstance(val, int):
                    config.permission.timeout_s = val
                else:
                    raise SystemExit(
                        f"Config error: permission.timeout_s must be a integer"
                    )

    if "compaction" in data:
        comp = data["compaction"]
        if not isinstance(comp, dict):
            raise SystemExit("Config error: [compaction] must be a table")
        unknown_comp: set[str] = set(comp.keys()) - {
            "auto_threshold",
            "tool_result_limit",
            "tool_result_keep",
        }

        if unknown_comp:
            raise SystemExit(
                f"Unknown [compaction] keys: {', '.join(sorted(unknown_comp))}"
            )

        if "auto_threshold" in comp:
            val = comp["auto_threshold"]
            if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
                raise SystemExit(
                    "Config error: compaction.auto_threshold must be between 0 and 1"
                )
            config.compaction.auto_threshold = float(val)
        if "tool_result_limit" in comp:
            val = comp["tool_result_limit"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit(
                    "Config error: compaction.tool_result_limit must be a positive integer"
                )
            config.compaction.tool_result_limit = val
        if "tool_result_keep" in comp:
            val = comp["tool_result_keep"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit(
                    "Config error: compaction.tool_result_keep must be a positive integer"
                )
            config.compaction.tool_result_keep = val


def _apply_env(config: CrispConfig) -> None:
    host = os.environ.get("CRISP_HOST")
    if host is not None:
        config.host = host

    port_str = os.environ.get("CRISP_PORT")
    if port_str is not None:
        try:
            config.port = int(port_str)
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_PORT must be an integer, got: {port_str!r}"
            )

    log_level = os.environ.get("CRISP_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("CRISP_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("CRISP_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format

    max_steps_str = os.environ.get("CRISP_MAX_STEPS")
    if max_steps_str is not None:
        try:
            val = int(max_steps_str)
            if val <= 0:
                raise SystemExit(
                    "Config error: CRISP_MAX_STEPS must be a positive integer,"
                    f" got: {max_steps_str!r}"
                )
                config.agent.max_steps = val
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_MAX_STEPS must be an integer, got: {max_steps_str}"
            )

    default_model = os.environ.get("CRISP_LLM_DEFAULT_MODEL")
    if default_model is not None:
        config.llm.default = default_model

    trace_enabled = os.environ.get("CRISP_TRACE_ENABLE")
    if trace_enabled is not None:
        config.trace.enabled = trace_enabled.lower() in ("1", "true", "yes")

    trace_file = os.environ.get("CRISP_TRACE_FILE", _DEFAULT_TRACE_FILE)
    if trace_file is not None:
        config.trace.file = trace_file

    trace_include_llm_payload = os.environ.get("CRISP_TRACE_INCLUDE_LLM_PAYLOAD")
    if trace_include_llm_payload is not None:
        config.trace.include_llm_payload = trace_include_llm_payload.lower() in (
            "1",
            "true",
            "yes",
        )

    default_model = os.environ.get("CRISP_LLM_DEFAULT_MODEL")
    if default_model is not None:
        config.llm.default_model = default_model

    trace_enabled = os.environ.get("CRISP_TRACE_ENABLED")
    if trace_enabled is not None:
        config.trace.enabled = trace_enabled.lower() not in ("0", "false", "no")

    trace_file = os.environ.get("CRISP_TRACE_FILE")
    if trace_file is not None:
        config.trace.file = trace_file

    trace_payload = os.environ.get("CRISP_TRACE_INCLUDE_LLM_PAYLOAD")
    if trace_payload is not None:
        config.trace.include_llm_payload = trace_payload.lower() not in (
            "0",
            "false",
            "no",
        )
    tokens_enabled = os.environ.get("CRISP_TUI_TOKENS_ENABLED")
    if tokens_enabled is not None:
        config.tui_config.tokens_enabled = tokens_enabled.lower() not in (
            "0",
            "false",
            "no",
        )

    perm_timeout = os.environ.get("CRISP_PERMISSION_TIMEOUT_S")
    if perm_timeout is not None:
        try:
            val = float(perm_timeout)
            if val < 0:
                raise SystemExit(
                    f"Config error: CRISP_PERMISSION_TIMEOUT_S must be >= 0, got: {perm_timeout!r}"
                )
            config.permission.timeout_s = val
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_PERMISSION_TIMEOUT_S must be a number, got: {perm_timeout!r}"
            )

    compact_threshold = os.environ.get("CRISP_COMPACT_THRESHOLD")
    if compact_threshold is not None:
        try:
            compact_threshold_val = float(compact_threshold)
            if not (0.0 <= compact_threshold_val <= 1.0):
                raise SystemExit(
                    f"Config error: CRISP_COMPACT_THRESHOLD must be between 0 and 1, got: {compact_threshold!r}"
                )
            config.compaction.auto_threshold = compact_threshold_val
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_COMPACT_THRESHOLD must be a number, got: {compact_threshold!r}"
            )

    compact_tool_limit = os.environ.get("CRISP_COMPACT_TOOL_LIMIT")
    if compact_tool_limit is not None:
        try:
            compact_tool_limit_val = int(compact_tool_limit)
            if compact_tool_limit_val <= 0:
                raise SystemExit(
                    f"Config error: CRISP_COMPACT_TOOL_LIMIT must be a positive integer, got: {compact_tool_limit!r}"
                )
            config.compaction.tool_result_limit = compact_tool_limit_val
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_COMPACT_TOOL_LIMIT must be an integer, got: {compact_tool_limit!r}"
            )

    compact_tool_keep = os.environ.get("CRISP_COMPACT_TOOL_KEEP")
    if compact_tool_keep is not None:
        try:
            compact_tool_keep_val = int(compact_tool_keep)
            if compact_tool_keep_val <= 0:
                raise SystemExit(
                    f"Config error: CRISP_COMPACT_TOOL_KEEP must be a positive integer, got: {compact_tool_keep!r}"
                )
            config.compaction.tool_result_keep = compact_tool_keep_val
        except ValueError:
            raise SystemExit(
                f"Config error: CRISP_COMPACT_TOOL_KEEP must be an integer, got: {compact_tool_keep!r}"
            )
