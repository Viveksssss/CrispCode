from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from crispcode.core.bus.envelope import HandleError
from crispcode.core.bus.events import (
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionMessageReceivedEvent,
    SessionResumedEvent,
    SessionIdleEvent,
    SkillInvokeEvent,
)
from crispcode.core.events.bus import EventBus
from crispcode.core.runs import new_runs_id
from crispcode.core.session.model import Session, SessionMode
from crispcode.core.session.store import SessionStore
from crispcode.core.skills.loader import SkillLoader

if TYPE_CHECKING:
    from crispcode.core.runner import AgentRunner
    from crispcode.core.llm.provider import LLMProvider

SESSION_NOT_FOUND = -32010
SESSION_CLOSED = -32011
SESSION_BUSY = -32012


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        runner_factory: Callable[[], AgentRunner],
        bus: EventBus,
        provider: LLMProvider | None = None,
    ):
        """初始化回哈u管理器,介入文件存储,runner工厂和事件总线"""
        self._store = store
        self._runner_factory = runner_factory
        self._bus = bus
        self._provider = provider
        self._sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._skill_loader = SkillLoader()

    async def create(self, mode: SessionMode, title: str = "") -> Session:
        """创建新 session 并写入 meta.json"""
        sid = f"session-{uuid.uuid4().hex[:12]}"
        ts = _now()
        session = Session(
            id=sid,
            mode=mode,
            status="active",
            title=title,
            created_at=ts,
            updated_at=ts,
            runs_ids=[],
        )
        self._sessions[sid] = session
        self._locks[sid] = asyncio.Lock()
        self._store.write_meta(session)
        await self._bus.publish(SessionCreatedEvent(session_id=sid, mode=mode, ts=ts))
        return session

    async def send_message(
        self, sid: str, content: str, *, runs_id: str | None = None
    ) -> str:
        """处理用户消息，追加 thread 并启动一次 agent run"""
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandleError(SESSION_BUSY, "session busy")
        async with lock:
            if session.status == "closed":
                raise HandleError(SESSION_CLOSED, "session already closed")
            if session.status == "idle":
                await self._bus.publish(SessionResumedEvent(session_id=sid, ts=_now()))

            self._store.append_message(sid, "user", content)
            await self._bus.publish(
                SessionMessageReceivedEvent(session_id=sid, content=content, ts=_now())
            )

            if not session.title:
                session.title = content[:30]

            runs_id = runs_id or new_runs_id()
            session.runs_ids.append(runs_id)
            session.updated_at = _now()
            self._store.write_meta(session)

            goal = content
            system_prompt_override: str | None = None
            tool_whitelist: list[str] | None = None
            if content.startswith("/"):
                parts = content[1:].split(None, 1)
                skill_name = parts[0]
                arguments = parts[1] if len(parts) > 1 else ""
                skill = self._skill_loader.resolve(skill_name)
                if skill is not None:
                    goal = self._skill_loader.render_prompt(skill, arguments)
                    system_prompt_override = skill.system_prompt_template
                    tool_whitelist = skill.allowed_tools or None
                    await self._bus.publish(
                        SkillInvokeEvent(
                            skill_name=skill_name,
                            arguments=arguments,
                            runs_id=runs_id,
                            ts=_now(),
                        )
                    )

            runner = self._runner_factory()
            await runner.run_and_capture(
                goal,
                runs_id=runs_id,
                session=session,
                store=self._store,
                system_prompt_override=system_prompt_override,
                tool_whitelist=tool_whitelist,
            )

            session.updated_at = _now()
            if session.mode == "one_shot":
                session.status = "closed"
                await self._bus.publish(
                    SessionClosedEvent(session_id=sid, ts=session.updated_at)
                )

            else:
                session.status = "idle"
                await self._bus.publish(
                    SessionIdleEvent(
                        session_id=sid, last_runs_id=runs_id, ts=session.updated_at
                    )
                )
            self._store.write_meta(session)
            return runs_id

    async def close(self, sid: str) -> None:
        """关闭指定 session 并更新 meta.json"""
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandleError(SESSION_BUSY, "session busy")
        async with lock:
            session.status = "closed"
            session.updated_at = _now()
            self._store.write_meta(session)
            await self._bus.publish(
                SessionClosedEvent(session_id=sid, ts=session.updated_at)
            )

    async def get_history(self, sid: str) -> list[dict[str, Any]]:
        """读取指定 session 的完整 thread 历史"""
        return self._store.read_messages(sid)

    def _get_session(self, sid: str) -> Session:
        """从内存索引取 session，不存在时抛 JSON-RPC 结构化错误"""
        session = self._sessions.get(sid)
        if session is None:
            raise HandleError(SESSION_NOT_FOUND, "session not found")
        return session

    async def compact(self, sid: str, focus: str = "") -> Any:
        """手动压缩指定 session 的 thread，将摘要持久化写入 thread.jsonl"""
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandleError(SESSION_BUSY, "session busy")
        if self._provider is None:
            raise HandleError(-32020, "provider not available for compaction")

        async with lock:
            from crispcode.core.bus.commands import SessionCompactResult
            from crispcode.core.compact.compactor import Compactor

            messages = self._store.read_messages(sid)
            session_dir = self._store.session_dir(sid)
            compactor = Compactor(self._bus, session_dir, sid)
            result = await compactor.compact_messages(
                messages, self._provider, focus=focus
            )
            if result is None:
                raise HandleError(-32021, "compaction failed or not beneficial")
            self._store.write_compacted(
                sid,
                [
                    {"role": "user", "content": result.summary_text},
                    {
                        "role": "assistant",
                        "content": "Understood, I'll continue from this summary.",
                    },
                ],
            )
            return SessionCompactResult(
                summary_tokens=result.summary_tokens,
                saved_tokens=max(
                    0, result.original_token_estimate - result.summary_tokens
                ),
            )
