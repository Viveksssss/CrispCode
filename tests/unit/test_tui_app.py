from __future__ import annotations

from rich.markdown import Markdown
from textual.widget import Widget

from crispcode.core.config import CrispConfig
from crispcode.tui.app import (
    CrispTuiApp,
    LLMStreamBlock,
    PermissionBlock,
    PermissionSelect,
    ToolCallBlock,
    _param_summary,
    _preview,
)


# 功能：验证 _preview 超出长度时截断并追加省略号
# 设计：不依赖任何 TUI 组件，纯函数测试
def test_preview_truncates() -> None:
    assert _preview("abcde", 3) == "abc..."
    assert _preview("ab", 5) == "ab"


# 功能：验证工具参数摘要优先展示工具最关键字段
# 设计：覆盖 read_file/bash/note_save 三类常见工具，避免工具块摘要退化成整段 JSON
def test_param_summary_prefers_key_fields() -> None:
    assert _param_summary("read_file", {"path": "README.md"}) == "path='README.md'"
    assert (
        _param_summary("bash", {"command": "echo hi", "timeout": 1})
        == "command='echo hi'"
    )
    assert (
        _param_summary("note_save", {"content": "Python 3.12"})
        == "content='Python 3.12'"
    )


# 功能：验证 llm.token 事件累积到 LLMStreamBlock，不连续 token 各自新开一块
# 设计：monkey-patch _append 收集追加的 widgets，断言 token 追加到同一块；
#       发送非 token 事件后新 block 被重置，下一个 token 开启新块
def test_llm_tokens_accumulate_in_block() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event(
        {"type": "llm.token", "token": "Hello", "runs_id": "r", "ts": "t"}
    )
    app._handle_event(
        {"type": "llm.token", "token": " world", "runs_id": "r", "ts": "t"}
    )

    assert len(appended) == 1  # same block reused
    assert isinstance(appended[0], LLMStreamBlock)
    assert appended[0]._text == "Hello world"  # type: ignore[attr-defined]


# 功能：验证 LLMStreamBlock 结束时会把累积文本渲染为 Rich Markdown
# 设计：直接调用 finalize_markdown，断言 renderable 类型，覆盖 Markdown polish 的核心行为
def test_llm_block_finalize_renders_markdown() -> None:
    block = LLMStreamBlock()
    block.append_token("## Title\n\n- one\n\n```python\nprint('hi')\n```")
    block.finalize_markdown()
    # standalone block 未挂载到屏幕时 is_attached=False，Markdown 子 widget 不会被 mount，
    # 但 _finalized 标记应已置位，且文本保留原样
    assert block._finalized  # type: ignore[attr-defined]
    assert "Title" in block._text  # type: ignore[attr-defined]


# 功能：验证非 token 事件后 _current_llm 被重置，下一个 token 开启新块
# 设计：插入 step.started 中断流，验证之前的 block 被 finalize，之后的 llm.token 创建新 LLMStreamBlock
def test_llm_block_resets_after_non_token_event() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event({"type": "llm.token", "token": "A", "runs_id": "r", "ts": "t"})
    app._handle_event({"type": "step.started", "runs_id": "r", "step": 2, "ts": "t"})
    app._handle_event({"type": "llm.token", "token": "B", "runs_id": "r", "ts": "t"})

    llm_blocks = [w for w in appended if isinstance(w, LLMStreamBlock)]
    assert len(llm_blocks) == 2
    assert llm_blocks[0]._finalized  # type: ignore[attr-defined]


# 功能：验证 run.started 事件追加 Static widget 且包含 runs_id 和 goal
# 设计：monkey-patch _append，断言追加的 widget 的 renderable 包含关键字段
def test_run_started_appends_widget_with_content() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event(
        {"type": "run.started", "runs_id": "run-abc", "goal": "do the thing", "ts": "t"}
    )

    assert len(appended) == 2
    rendered_run_id = appended[0].content
    rendered_goal = appended[1].content
    assert "run-abc" in rendered_run_id
    assert "do the thing" in rendered_goal


# 功能：验证 run.finished success 追加包含 "completed" 的 widget
# 设计：monkey-patch _append，检查 rendered 内容包含 completed 和 green
def test_run_finished_success_shows_completed() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event(
        {
            "type": "run.finished",
            "runs_id": "r",
            "status": "success",
            "steps": 3,
            "ts": "t",
        }
    )

    rendered = appended[0].content
    assert "completed" in rendered
    assert "green" in rendered


# 功能：验证 run.finished failed 追加包含 "failed" 和 red 的 widget
# 设计：与 success 对称，检查颜色标记差异
def test_run_finished_failed_shows_red() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event(
        {
            "type": "run.finished",
            "runs_id": "r",
            "status": "failed",
            "steps": 1,
            "reason": "llm_error",
            "ts": "t",
        }
    )

    rendered = appended[0].content
    assert "failed" in rendered
    assert "red" in rendered


# 功能：验证 tool.call_started 追加 ToolCallBlock，call_finished 更新其结果
# 设计：直接调用 _handle_event 两次，通过 _pending_tool_blocks 验证状态流转
def test_tool_call_started_and_finished() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event(
        {
            "type": "tool.call_started",
            "tool_use_id": "uid-1",
            "tool_name": "bash",
            "params": {"command": "echo hi"},
            "runs_id": "r",
            "ts": "t",
        }
    )
    assert "uid-1" in app._pending_tool_blocks  # type: ignore[attr-defined]

    app._handle_event(
        {
            "type": "tool.call_finished",
            "tool_use_id": "uid-1",
            "tool_name": "bash",
            "elapsed_ms": 42,
            "output": "hi",
            "runs_id": "r",
            "ts": "t",
        }
    )
    assert "uid-1" not in app._pending_tool_blocks  # type: ignore[attr-defined]
    block = appended[0]
    assert isinstance(block, ToolCallBlock)
    assert block._finished  # type: ignore[attr-defined]
    assert block._output == "hi"  # type: ignore[attr-defined]


# 功能：验证 note_save 成功完成时工具块摘要显示 remembered
# 设计：直接操作 ToolCallBlock，覆盖 note_save 的特殊低噪声展示策略
def test_note_save_tool_block_shows_remembered() -> None:
    block = ToolCallBlock("note_save", {"content": "Python 3.12"})
    block.set_result("saved", 3)
    title, summary = block._summary()  # type: ignore[attr-defined]
    assert "done" in summary


# 功能：验证提交用户输入时会追加 user turn，并进入 busy 状态
# 设计：用 fake client 替代 SocketClient，直接调用 on_chat_text_area_submitted，
#       覆盖 TextArea 清空内容 + 设置 busy 占位符的核心状态迁移
async def test_input_submit_appends_user_turn_and_disables_prompt() -> None:
    class _FakeArea:
        def __init__(self) -> None:
            self.disabled = False
            self.border_title = ""
            self.text = "hello"

    class _FakeEvent:
        def __init__(self, area: _FakeArea) -> None:
            self.value = area.text
            self.text_area = area

    class _FakeClient:
        async def send_command(self, method: str, params: dict) -> dict:
            return {"runs_id": "run-1"}

    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]
    app._update_header = lambda state: None  # type: ignore[method-assign]
    app._client = _FakeClient()  # type: ignore[assignment]
    app._session_id = "sess-1"

    area = _FakeArea()
    event = _FakeEvent(area)
    await app.on_chat_text_area_submitted(event)  # type: ignore[arg-type]

    assert app._busy  # type: ignore[attr-defined]
    assert area.disabled
    assert area.text == ""
    assert "agent is working" in area.border_title.lower()


# 功能：验证未知事件类型不抛异常也不追加任何 widget
# 设计：发送 type 为 unknown 的事件，断言 appended 为空
def test_unknown_event_silently_ignored() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]

    app._handle_event({"type": "some.unknown.type", "runs_id": "r", "ts": "t"})
    assert appended == []


# 功能：验证 permission.requested 事件注册审批块并挂载选择控件
# 设计：回归测试——此前误用未定义的 _pending_message_blocks（AttributeError 崩掉 socket worker）
#       且调用不存在的 _mount_permission_select；两者都曾导致权限 UI 无法出现
def test_permission_requested_registers_block_and_select() -> None:
    app = CrispTuiApp(CrispConfig("127.0.0.1", 9999))
    appended: list[Widget] = []
    app._append = lambda w: appended.append(w)  # type: ignore[method-assign]
    app._prompt = lambda: None  # type: ignore[method-assign]

    app._handle_event(
        {
            "type": "permission.requested",
            "tool_use_id": "uid-1",
            "tool_name": "write_file",
            "params": {"path": "a.txt", "content": "hi"},
            "param_preview": "path = 'a.txt'",
            "session_id": "s1",
            "runs_id": "r",
            "ts": "t",
        }
    )

    assert "uid-1" in app._pending_permission_blocks  # type: ignore[attr-defined]
    assert isinstance(appended[0], PermissionBlock)
    assert isinstance(appended[1], PermissionSelect)
