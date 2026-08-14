from __future__ import annotations

import asyncio
from datetime import datetime, UTC
import fnmatch
import json
import logging
from pathlib import Path
import signal
import time
from typing import Any

from pydantic import BaseModel

import crispcode
from crispcode.core.bus.commands import (
    PermissionRespondCommand,
    PermissionRespondResult,
    PongResult,
    AgentRunResult,
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    SessionCloseCommand,
    SessionCloseResult,
    SessionCompactCommand,
    SessionCreateCommand,
    SessionCreateResult,
    SessionGetHistoryCommand,
    SessionGetHistoryResult,
    SessionSendMessageCommand,
    SessionSendMessageResult,
)
from crispcode.core.bus.envelope import EventPushEnvelope
from crispcode.core.config import CrispConfig, CrispConfig, get_config
from crispcode.core.events.bus import EventBus
from crispcode.core.llm.provider import AnthropicProvider
from crispcode.core.logging import setup_logging
from crispcode.core.runner import AgentRunner
from crispcode.core.runs import events_file, new_runs_id, run_dir_old
from crispcode.core.trace.record import TraceRecord
from crispcode.core.trace.writer import TraceWriter
from crispcode.core.transport.ipc_broadcaster import IpcEventBroadcaster
from crispcode.core.transport.socket_server import SocketServer, get_connection_writer
from crispcode.core.session import SessionManager, SessionStore
from crispcode.core.permissions.manager import PermissionManager
from crispcode.core.permissions.storage import load_policy_file

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CoreApp:
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._bus = EventBus()
        self._broadcaster: IpcEventBroadcaster | None = None
        self._config: CrispConfig | None = None
        self._trace: TraceWriter | None = None
        self._running_runs: set[asyncio.Task[None]] = set()
        self._sessions: SessionManager | None = None
        self._permission_manager: PermissionManager | None = None

    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        """处理 core.ping 请求，返回服务版本、运行时长和接收时间"""
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=crispcode.__version__,
            uptime_ms=int(time.monotonic() - self._start_time) * 1000,
            received_at=datetime.now(UTC).isoformat(),
        )

    async def _trace_event_handler(self, event: BaseModel) -> None:
        assert self._trace is not None
        event_dict = event.model_dump()
        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE",
                layer="event",
                kind="event",
                runs_id=event_dict.get("runs_id"),
                data=event_dict,
            )
        )

    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        """启动一次 agent run：立即返回 runs_id，后台 task 执行 runner.run()"""
        assert self._sessions is not None
        cmd = AgentRunCommand.model_validate(params)
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:30])
        runs_id = new_runs_id()
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, runs_id=runs_id)
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        return AgentRunResult(runs_id=runs_id)

    async def _session_create_handler(
        self, params: dict[str, Any]
    ) -> SessionCreateResult:
        """创建 chat 或 one_shot session，并返回 session_id"""
        assert self._sessions is not None
        cmd = SessionCreateCommand.model_validate(params)
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        return SessionCreateResult(session_id=session.id, status=session.status)

    async def _session_send_handler(
        self, params: dict[str, Any]
    ) -> SessionSendMessageResult:
        """向 session 发送一条用户消息并同步等待对应 run 完成"""
        assert self._sessions is not None
        cmd = SessionSendMessageCommand.model_validate(params)
        run_id = await self._sessions.send_message(cmd.session_id, cmd.content)
        return SessionSendMessageResult(run_id=run_id)

    async def _session_history_handler(
        self, params: dict[str, Any]
    ) -> SessionGetHistoryResult:
        """返回 session 的完整 Anthropic messages 历史"""
        assert self._sessions is not None
        cmd = SessionGetHistoryCommand.model_validate(params)
        messages = await self._sessions.get_history(cmd.session_id)
        return SessionGetHistoryResult(messages=messages)

    async def _session_compact_handler(
        self, params: dict[str, Any]
    ) -> SessionCloseResult:
        """手动压缩 session thread，将摘要持久化写入 thread.jsonl"""
        assert self._sessions is not None
        cmd = SessionCompactCommand.model_validate(params)
        result = await self._sessions.compact(cmd.session_id, cmd.focus)
        return result

    async def _session_close_handler(
        self, params: dict[str, Any]
    ) -> SessionCloseResult:
        """关闭 session 并返回 closed 状态"""
        assert self._sessions is not None
        cmd = SessionCloseCommand.model_validate(params)
        await self._sessions.close(cmd.session_id)
        return SessionCloseResult(status="closed")

    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        """注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流"""
        cmd = EventSubscribeCommand.model_validate(params)
        writer = get_connection_writer()

        # 已回放的事件条数
        replayed_count = 0
        if cmd.replayed_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replayed_from_run, writer, cmd.topics
            )

        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        return EventSubscribeResult(
            subscription_id=sub_id, replayed_count=replayed_count
        )

    async def _permission_respond_handler(
        self, params: dict[str, Any]
    ) -> PermissionRespondResult:
        """接收客户端权限审批响应，resolve 对应挂起的 Future"""
        cmd = PermissionRespondCommand.model_validate(params)
        logger.info(
            "permission.respond received tool_use_id=%s decision=%s",
            cmd.tool_use_id,
            cmd.decision,
        )
        if self._permission_manager is None:
            logger.error("permission.respond: PermissionManager not initialized")
            return PermissionRespondResult()
        self._permission_manager.respond(cmd.tool_use_id, cmd.decision)
        return PermissionRespondResult()

    async def _replay_events(
        self, runs_id: str, writer: asyncio.StreamWriter, topics: list[str]
    ) -> int:
        path = run_dir_old(runs_id) / "events.jsonl"
        if not path.exists():
            for candidate in (
                Path("~/.crispcode/session")
                .expanduser()
                .glob(f"*/runs/{runs_id}/events.jsonl")
            ):
                path = candidate
                break
        if not path.exists():
            return 0

        count = 0
        for line in path.read_text().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type", "")
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue

            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1
        if count:
            await writer.drain()
        return count

    async def run(self) -> None:
        self._start_name = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)

        if self._config.trace.enabled:
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = TraceWriter(trace_path)
            await self._trace.start()
            self._bus.subscribe(self._trace_event_handler)

        policy_file = Path("~/.crispcode/policy.toml").expanduser()
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )

        logger.info(
            "permission manager:timeout_s=%.1f persistent=%d entries",
            self._config.permission.timeout_s,
            len(load_policy_file(policy_file)),
        )

        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        self._bus.subscribe(self._broadcaster.handle)
        session_root = Path("~/.crispcode/session").expanduser()
        store = SessionStore(session_root)
        compact_provider = AnthropicProvider(self._config.llm.default_model)
        self._sessions = SessionManager(
            store,
            runner_factory=lambda: AgentRunner(
                self._config,
                bus=self._bus,
                trace=self._trace,
                permission_manager=self._permission_manager,
            ),
            bus=self._bus,
            provider=compact_provider,
        )

        server = SocketServer(
            self._config.host, self._config.port, self._broadcaster, trace=self._trace
        )
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)
        server.register("session.create", self._session_create_handler)
        server.register("session.send_message", self._session_send_handler)
        server.register("session.get_history", self._session_history_handler)
        server.register("session.close", self._session_close_handler)
        server.register("permission.respond", self._permission_respond_handler)
        server.register("session.compact", self._session_compact_handler)

        addr = await server.start()
        logger.info("crisp-core %s listening addr=%s", crispcode.__version__, addr)
        logger.info("config: %s", self._config)

        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        await shutdown.wait()

        logger.info("shutting down")

        for run_task in list(self._running_runs):
            run_task.cancel()

        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)

        await server.stop()

        if self._trace is not None:
            await self._trace.stop()


def run() -> None:
    asyncio.run(CoreApp().run())


"""
一次问答的生命周期

  用户每次提问：
    JSON-RPC agent.run(goal="...")
        │
        ▼
    SocketServer._read_line() -> _dispatch()     
        │
        ▼
    _agent_run_handler()
        │
        ├─ new_run_id()                    ← 创建这次 run 的唯一 ID
        ├─ AgentRunner(...)                ← 创建（不是复用）
        ├─ asyncio.create_task(runner.run) ← 后台启动
        └─ return run_id                   ← 立刻回复客户端
                                                │
                                                ▼
                                          runner.run()
                                                │
                                                ├─ 创建 EventBus
                                                ├─ 创建 AnthropicProvider
                                                ├─ 创建 ToolRegistry（8 个工具）
                                                ├─ 创建 AgentLoop
                                                ├─ 创建 ExecutionContext
                                                │
                                                └─ await loop.run(context)
                                                       │
                                                       ├─ step 1: LLM 思考 -> 调工具 -> 记录
                                                       ├─ step 2: LLM 思考 -> 调工具 -> 记录
                                                       ├─ ...
                                                       └─ step N: LLM 说 end_turn -> 完成

  run 结束 -> AgentRunner.run() 返回 -> 协程结束 -> 这次任务彻底结束

  关键点

  - AgentRunner 不是常驻对象，每次 agent.run 创建一个新的，跑完就销毁
  - AgentLoop 的 while not context.is_done() 循环，就是一次问答里的多步对话
  - 一次问答 = 一次 run = 一个 AgentRunner = 一个 AgentLoop = 多个 step
  - 用户下次提问 = 又一次全新的 agent.run = 又一个新的 AgentRunner
  
  
  
  daemon 里一直跑着的东西
  
    ┌──────────────────────┬───────────────────────────────┬────────────────────────┐
    │         组件         │           循环方式            │         干什么         │
    ├──────────────────────┼───────────────────────────────┼────────────────────────┤
    │ SocketServer         │ await reader.readline()       │ 等客户端连接、读命令   │
    ├──────────────────────┼───────────────────────────────┼────────────────────────┤
    │ CoreApp              │ await shutdown.wait()         │ 等 SIGINT/SIGTERM 信号 │
    ├──────────────────────┼───────────────────────────────┼────────────────────────┤
    │ TraceWriter._drain() │ while True: await queue.get() │ 等 trace 记录写入文件  │
    └──────────────────────┴───────────────────────────────┴────────────────────────┘
  
    daemon 启动
        │
        ├─ SocketServer._read_loop()     ← 无限循环，等命令来
        ├─ TraceWriter._drain()          ← 无限循环，等 trace 记录来
        └─ CoreApp: await shutdown.wait() ← 等退出信号
  
    其他所有东西都是"来了才跑，跑完就没了"：
  
    AgentRunner.run()       ← agent.run 来了才创建，跑完就销毁
    AgentLoop.run()         ← 跟着 AgentRunner 一起生一起死
    invoke_tool()           ← 工具调用时创建，返回结果就结束
  
    类比
  
    SocketServer  = 前台接待（一直值班，有客人来才带路）
    CoreApp       = 经理（等下班信号）
    TraceWriter   = 记录员（等要记录的东西）
  
    AgentRunner   = 临时项目组（来了任务才组建，做完就解散）
    AgentLoop     = 项目组的每日会议（项目期间反复开，项目结束就停）

"""
