from __future__ import annotations

import asyncio
from asyncio import events
import json
import logging
from typing import Any
from textual.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Label, Markdown, RichLog, Static, TextArea
from textual.widget import Widget
from crispcode.core.config import CrispConfig
from textual.containers import VerticalScroll
from textual.message import Message
from textual.css.query import NoMatches
from crispcode.core.transport.socket_client import IpcError, SocketClient

logger = logging.getLogger(__name__)


def _preview(s: str, n: int) -> str:
    """截断预览文字"""
    return s[:n] + "..." if len(s) > n else s


def _params_str(params: dict[str, Any]) -> str:
    """将字典参数序列化为格式化的JSON字符串"""
    return json.dumps(params, ensure_ascii=False)


def _param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    """从工具参数中提取最适合摘要展示的关键字段"""
    keys_by_tool = {
        "read_file": ("path",),
        "write_file": ("path",),
        "list_dir": ("path", "max_depth"),
        "bash": ("command",),
        "note_save": ("content",),
    }
    keys = keys_by_tool.get(tool_name, ())
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    return _preview(", ".join(parts), max_len)


class LLMStreamBlock(Static):
    """在同一个Static Widget 中累计LLM流式token."""

    DEFAULT_CSS = "LLMStreamBlock { padding: 0 2; color: $text; }"

    def __init__(self) -> None:
        super().__init__("")
        self._text = ""
        self._finalized = False

    def append_token(self, token: str) -> None:
        if self._finalized:
            return
        self._text += token
        self.update(self._text)

    def finilize_markdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._text.strip():
            self.mount(Markdown(self._text))


class ToolCallBlock(Static):
    """可折叠的工具调用块: 折叠式显示摘要,点击后显示完整的params和output"""

    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 2; color: $text-muted; border: #CCCCFF; }
    ToolCallBlock.error { height: auto; padding: 0 2; color: $text-muted; border: red; }
    ToolCallBlock > .tool_title { color: $text-muted; padding-left: 0; }
    ToolCallBlock > .summary { color: $text-muted; padding-left: 4; }
    ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
    ToolCallBlock.expanded > .detail { display: block; padding-left: 4;}
    """

    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        """初始化工具调用信息"""
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._params_full = _params_str(params)
        self._output = ""
        self._elapsed_ms = 0
        self._is_error = False
        self._finished = False
        self._tool_title = ""
        self._summarys = ""
        self._expanded = ""

    def compose(self) -> ComposeResult:
        self._tool_title, self._summarys = self._summary()
        yield Static(self._tool_title, classes="tool_title")
        yield Static(self._summarys, classes="summary")
        yield Static("", classes="detail", markup=True)

    def _summary(self) -> tuple[str, str]:
        """生成摘要行文本"""
        params_pre = _preview(self._params_full, 30)
        icon = "[bold yellow]✎[/bold yellow]"
        title = f"{icon} [#FFCCFF]{self._tool_name}[/#FFCCFF]"
        hint = "[dim](▸ click to toggle)[/dim]" if len(self._output) > 30 else ""
        title += hint

        line = f"[dim]{params_pre}[/dim]"
        if self._finished:
            color = "red" if self._is_error else "green"
            status = "failed" if self._is_error else "done"
            line += f"[{color}]{status}[/{color}] [dim]{self._elapsed_ms}ms[/dim]"
        # if self._finished:
        #     out_pre = _preview(self._output, 30)
        #     color = "red" if self._is_error else "dim"

        #     line += (
        #         f"\n[{color}]{out_pre}[{color}]"
        #         f"    [dim]{self._elapsed_ms}ms[/dim]\n"
        #     )

        return title, line

    def set_result(
        self, output: str, elapsed_ms: int, *, is_error: bool = False
    ) -> None:
        """工具调用完成时更新结果并刷新摘要（widget 未挂载时跳过 DOM 更新）"""
        self._output = output
        self._elapsed_ms = elapsed_ms
        self._is_error = is_error
        self._finished = True
        self._tool_title, self._summarys = self._summary()
        if self.children:
            self.query_one(".tool_title", Static).update(self._tool_title)
            self.query_one(".summary", Static).update(self._summarys)

        if is_error:
            self.add_class("error")

    def on_click(self) -> None:
        if not self._finished:
            return
        summary = self.query_one(".summary", Static)
        if "expanded" in self.classes:
            summary.update(self._summarys)
            self.remove_class("expanded")
        else:
            summary.update("")
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params:[/dim]\n    {escape(self._params_full)}\n"
                f"[dim]output:[/dim]\n\n{escape(self._output)}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )

            self.add_class("expanded")


class PermissionSelect(Static):
    """内联权限选择控件：挂载在日志流中，键盘焦点无需 ModalScreen。"""

    can_focus = True

    DEFAULT_CSS = """
    PermissionSelect{
        height:auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once", "Allow once", "y / 1"),
        ("always_allow", "Always allow", "a / 2"),
        ("deny_once", "Deny", "n / 3"),
        ("always_deny", "Always deny", "d / 4"),
    )
    _KEY_MAP: dict[str, str] = {
        "y": "allow_once",
        "1": "allow_once",
        "a": "always_allow",
        "2": "always_allow",
        "n": "deny_once",
        "3": "deny_once",
        "d": "always_deny",
        "4": "always_deny",
    }

    class Decided(Message):
        """# 用户作出权限决策时发布，携带工具 ID 和决策字符串"""

        def __init__(
            self, widget: "PermissionSelect", tool_use_id: str, decision: str
        ) -> None:
            """
            "PermissionSelect" ✅ 延迟求值，Python 会在运行时再解析,因为此时PermissionSelect还没完全创建.
            初始化决策消息，存储控件引用、工具 ID 和决策
            """
            self.widget = widget
            self.tool_use_id = tool_use_id
            self.decision = decision
            super().__init__()

    def __init__(self, tool_use_id: str) -> None:
        """初始化控件，存储工具 ID（用于 IPC 回复）"""
        super().__init__()
        self._tool_use_id = tool_use_id
        self._cursor = 0

    def on_mount(self) -> None:
        self.update(self._render_ui())
        self.focus()
        logger.debug(
            "PermissionSelect.on_mout can_focus=%s focusd_after=%r",
            self.can_focus,
            self.app.focused,
        )
        self.app.call_after_refresh(self._log_deferred_focus)

    def _log_deferred_focus(self) -> None:
        """在下一帧记录焦点是否真正转移到本控件"""
        logger.debug(
            "PermissionSelect.deferred_focus  app.focused=%r  has_focus=%s  focusable=%s",
            self.app.focused,
            self.has_focus,
            self.focusable,
        )

    # 焦点到达时记录，用于确认 focus() 是否真正生效
    def on_focus(self, event: events.Focus) -> None:
        logger.debug(
            "PermissionSelect.on_focus  has_focus=%s  app.focused=%r",
            self.has_focus,
            self.app.focused,
        )

    # 焦点离开时记录，用于追踪是否被其他控件抢走焦点
    def on_blur(self, event: events.Blur) -> None:
        logger.debug("PermissionSelect.on_blur  app.focused=%r", self.app.focused)

    # 生成带光标高亮的选项列表文本
    def _render_ui(self) -> str:
        lines: list[str] = []
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(
                    f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]"
                )
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        return "\n".join(lines)

    # 方向键导航；快捷键直接选择；enter 确认光标位置
    def on_key(self, event: events.Key) -> None:
        logger.debug(
            "PermissionSelect.on_key  key=%r  char=%r", event.key, event.character
        )
        key = event.key
        if key in ("up", "k"):
            event.stop()
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key in ("down", "j"):
            event.stop()
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key == "enter":
            event.stop()
            self._pick(self._CHOICES[self._cursor][0])
        else:
            decision = self._KEY_MAP.get(key)
            if decision is not None:
                event.stop()
                self._pick(decision)

    # 发布决策消息，由宿主 App 负责 IPC 回复和控件清理
    def _pick(self, decision: str) -> None:
        logger.debug("PermissionSelect._pick  decision=%s", decision)
        self.post_message(self.Decided(self, self._tool_use_id, decision))


class PermissionBlock(Static):
    """日志里的权限审批摘要"""

    _LABEL_MAP: dict[str, str] = {
        "allow_once": "allowed (once)",
        "always_allow": "always allowed",
        "deny_once": "denied",
        "always_deny": "always denied",
        "timeout": "⏱ timed out",
    }
    LABEL_MAP = _LABEL_MAP

    # 子类提交消息：用户作出权限决策时发布
    class Resolved(Message):
        def __init__(self, block: PermissionBlock, decision: str) -> None:
            self.block = block
            self.decision = decision
            super().__init__()

    # 初始化审批块，记录工具 ID、名称和参数预览
    def __init__(self, tool_use_id: str, tool_name: str, param_preview: str) -> None:
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._param_preview = param_preview
        self._resolved = False
        super().__init__(self._pending_text(), classes="log-line")

    def _pending_text(self) -> str:
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        return f"[bold red]? permission[/bold red]  [bold]{self._tool_name}[/bold]{preview}"

    # 将块收缩为单行摘要并发布 Resolved 消息
    def _resolve(self, decision: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        allowed = decision in ("allow_once", "always_allow")
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        label = self._LABEL_MAP.get(decision, decision)
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        self.post_message(self.Resolved(self, decision))


class ChatTextArea(TextArea):
    """支持Enter提交,Cmd/Shift/+Enter换行"""

    DEFAULT_CSS = """
    ChatTextArea {
        height: auto;
        min-height: 3;
        max-height: 12;
        border: round $surface-lighten-2;
        background: $background;
        padding: 0 1;
        margin: 1 2;
        scrollbar-size-vertical: 1;
        padding-left:1;
        padding-right:1;
    }
    ChatTextArea:focus {
        border: round $accent;
        background: $background;
    }
    """

    class Submitted(Message):
        def __init__(self, area: ChatTextArea) -> None:
            self.text_area = area
            self.value = area.text
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        """Enter 提交；Cmd/Shift/Alt+Enter 插入换行；其余键交回 TextArea 默认行为"""
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            if self.text.strip():
                self.post_message(self.Submitted(self))

        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return
        await super()._on_key(event)


class CrispTuiApp(App[None]):
    """CrispCode 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    TITLE = "CrispCode TUI"
    BINDINGS = [Binding("q", "quit", "Quit")]
    CSS = """
    Screen { background: $background; }
    #header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #log-view {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    Static.run-id { color: cyan; padding: 1 2 0 2;  }
    Static.run-header { color: $text; padding: 0 1 0 2; background: white;   /* 背景色 */ }
    Static.step-divider { color: $text-muted; padding: 0 2; }
    Static.run-ok { color: green; padding: 0 2 1 2; }
    Static.run-err { color: red; padding: 0 2 1 2; }
    Static.usage { padding: 0 2; }
    Static.log-line { padding: 0 2; }
    
    """

    def __init__(
        self, config: CrispConfig, replayed_runs_id: str | None = None
    ) -> None:
        """初始化连接参数和 token 缓冲区"""
        super().__init__()
        self._host = config.host
        self._port = config.port
        self._replayed_runs_id = replayed_runs_id
        self._token_buf = ""
        self._client: SocketClient | None = None
        self._current_llm: LLMStreamBlock | None = None
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}
        self._session_id: str | None = None
        self._config = config
        self._busy = False
        self._pending_permission_blocks: dict[str, PermissionBlock] = {}

    def compose(self) -> ComposeResult:
        """构建 UI：顶部状态栏 + 可滚动事件日志"""
        yield Label("[bold]CrispCode[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def on_mount(self) -> None:
        """挂载后启动 socket 连接 worker"""
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    def on_key(self, event: events.Key) -> None:
        logger.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        if not self._pending_permission_blocks:
            return
        try:
            select = self.query_one(PermissionSelect)
            if select.has_focus:
                return  # PermissionSelect 有焦点时自行处理，事件不会冒泡到这里
            key = event.key
            decision = PermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()
                select._pick(decision)
            elif key in ("up", "k"):
                event.stop()
                select._cursor = (select._cursor - 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key in ("down", "j"):
                event.stop()
                select._cursor = (select._cursor + 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key == "enter":
                event.stop()
                select._pick(PermissionSelect._CHOICES[select._cursor][0])
        except Exception:
            pass

    async def on_permission_select_decided(self, msg: PermissionSelect.Decided) -> None:
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        logger.info(
            "permission decided tool_use_id=%s decision=%s", tool_use_id, decision
        )
        try:
            msg.widget.remove()
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._resolve(decision)
            if self._client is not None:
                try:
                    await self._client.send_command(
                        "permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (IpcError, RuntimeError, OSError):
                    pass
            if not self._pending_permission_blocks:
                p = self._prompt()
                if p is not None:
                    p.disabled = False
                    p.read_only = False
                    p.border_title = (
                        "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    )
                    p.focus()
        except Exception:
            logger.exception(
                "on_permission_select_decided failed tool_use_id=%s", tool_use_id
            )

    async def action_quit(self) -> None:
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command(
                    "session.close", {"session_id": self._session_id}
                )
            except (IpcError, RuntimeError, OSError):
                self._append(
                    Static("[yellow]warning: failed to close session[/yellow]")
                )
        self.exit()

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """将输入框提交内容发送给当前 chat session"""
        content = event.value.strip()
        if not content:
            return
        if self._client is None or self._session_id is None or self._busy:
            self._append(
                Static("[yellow]agent busy or disconnected[/yellow]"),
                classes="log-line",
            )
            return
        self._busy = True
        prompt = event.text_area
        prompt.text = ""
        prompt.disabled = True
        prompt.border_title = "agent is working"
        self._update_header("running")

        self.run_worker(
            self._do_send_message(content), name="send_message", exclusive=False
        )

    def _prompt(self) -> ChatTextArea | None:
        """安全获取输入框，便于组件测试中未挂载时跳过 UI 操作"""
        try:
            return self.query_one("#prompt", ChatTextArea)
        except NoMatches:
            return None

    async def _do_send_message(self, content: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except (IpcError, RuntimeError, OSError) as e:
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = (
                    "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                )
            self._update_header("ready")
            self._append(Static(f"[red]send error: {e}[/red]", classes="log-line"))

    def _update_header(self, state: str) -> None:
        """根据连接和运行状态刷新顶部标题"""
        try:
            header = self.query_one("#header", Label)
        except NoMatches:
            return
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        color = {
            "ready": "green",
            "running": "yellow",
            "disconnected": "red",
            "connecting": "dim",
        }.get(state, "dim")

        header.update(
            f"[bold]CrispCode[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}  [{color}]{state}[/{color}]"
        )

    def _append(self, widget: Widget) -> None:
        """向日志视图追加一个 widget 并滚动到底部"""
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.scroll_relative(y=10)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    def _break_llm(self) -> None:
        """结束当前 LLM 流式块（下一个 token 将开启新块）"""
        if self._current_llm is not None:
            self._current_llm.finilize_markdown()  # ✅ 先 finalize
            self._current_llm = None

    async def _socket_loop(self) -> None:
        """管理 SocketClient 生命周期：连接、订阅、接收事件、断线重连"""
        header = self.query_one("#header", Label)
        while True:
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                self._update_header("disconnected")
                await asyncio.sleep(2)
                continue
            self._client = client
            self._update_header("connecting")
            loop_task = asyncio.create_task(client.run_event_loop())

            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            client.on_event(on_event)
            prompt = self._prompt()
            try:
                params: dict[str, Any] = {
                    "topics": [
                        "run.*",
                        "step.*",
                        "tool.*",
                        "permission.*",
                        "llm.token",
                        "llm.usage",
                        "log.*",
                        "session.*",
                    ],
                    "scope": "global",
                }
                if self._replayed_runs_id is not None:
                    params["replayed_from_run"] = self._replayed_runs_id
                await client.send_command("event.subscribe", params)
                created = await client.send_command("session.create", {"mode": "chat"})
                self._session_id = str(created["session_id"])

                if prompt is not None:
                    prompt.disabled = False
                    prompt.border_title = (
                        "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    )

                    prompt.focus()
                self._update_header("ready")

                await loop_task
            except IpcError as e:
                header.update(
                    f"[bold]CrispCode[/bold] [red]subscribe error: {e}[[/red]"
                )
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._session_id = None
                if prompt is not None:
                    prompt.disabled = True
                    prompt.border_title = "disconnected, retrying..."
                self._break_llm()
                await client.close()

            self._update_header("disconnected")
            await asyncio.sleep(2)

    def _handle_event(self, event: dict[str, Any]) -> None:
        try:
            t = event.get("type", "")
            if t == "llm.token":
                token = event.get("token", "")
                if self._current_llm is None:
                    llm_block = LLMStreamBlock()
                    self._append(llm_block)
                    self._current_llm = llm_block
                self._current_llm.append_token(token)
                return

            self._break_llm()
            if t in ("session.resumed", "session.idle"):
                self._busy = False
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.read_only = False
                    prompt.border_title = (
                        "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    )

                    prompt.focus()
                self._update_header("ready")
            elif t == "session.closed":
                self._busy = False
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.read_only = False
                    prompt.border_title = "session closed"
                self._update_header("disconnected")

            elif t == "run.started":
                runs_id = event.get("runs_id", "")
                goal = event.get("goal", "")
                self._append(
                    Static(
                        f"[bold cyan]▶ run[/bold cyan]  [dim]{runs_id}[/dim]",
                        classes="run-id",
                    )
                )
                self._append(
                    Static(
                        f"[#FF6699]✴[/#FF6699] [black]{goal}[black]",
                        classes="run-header",
                    )
                )

            elif t == "step.started":
                step = event.get("step", "")
                self._append(
                    Static(
                        f"[#00CC33]۞[/#00CC33] [#00CC33]step {step}[/#00CC33]",
                        classes="step-divider",
                    )
                )

            elif t == "tool.call_started":
                tool_use_id = str(event.get("tool_use_id", ""))
                tool_name = str(event.get("tool_name", ""))
                params = event.get("params") or {}
                tc_block = ToolCallBlock(tool_name, params)
                self._pending_tool_blocks[tool_use_id] = tc_block
                self._append(tc_block)

            elif t == "tool.call_finished":
                tool_use_id = str(event.get("tool_use_id", ""))
                elapsed_ms = int(event.get("elapsed_ms") or 0)
                output = str(event.get("output") or "")
                if tool_use_id in self._pending_tool_blocks:
                    tc_done = self._pending_tool_blocks.pop(tool_use_id)
                    tc_done.set_result(output, elapsed_ms)

            elif t == "tool.call_failed":
                tool_use_id = str(event.get("tool_use_id", ""))
                elapsed_ms = int(event.get("elapsed_ms") or 0)
                error_msg = str(event.get("error_message") or "")
                if tool_use_id in self._pending_tool_blocks:
                    tc_done = self._pending_tool_blocks.pop(tool_use_id)
                    tc_done.set_result(error_msg, elapsed_ms, is_error=True)

            elif t == "run.finished":
                status = event.get("status", "")
                steps = event.get("steps", 0)
                reason = event.get("reason") or ""
                if status == "success":
                    self._append(
                        Static(
                            f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                            classes="run-ok",
                        )
                    )
                else:
                    detail = f"  [dim]{reason}[/dim]" if reason else ""
                    self._append(
                        Static(
                            f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                            classes="run-err",
                        )
                    )

                self._append(Static("-" * (self.size.width - 10)))

            elif t == "llm.usage":
                if self._config.tui_config.tokens_enabled:
                    self._append(
                        Static(
                            f"[dim]   tokens  "
                            f"in={event.get('input_tokens')} "
                            f"out={event.get('output_tokens')} "
                            f"cache={event.get('cache_read_input_tokens')}[/dim]",
                            classes="usage",
                        )
                    )
            elif t == "permission.requested":
                tool_use_id = str(event.get("tool_use_id", ""))
                tool_name = str(event.get("tool_name", ""))
                param_preview = str(event.get("param_preview", ""))
                try:
                    _focused_repr = repr(self.focused)
                except Exception:
                    _focused_repr = "?"
                logger.info(
                    "permission.requested tool=%s id=%s  app.focused=%s",
                    tool_name,
                    tool_use_id,
                    _focused_repr,
                )
                perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
                self._pending_permission_blocks[tool_use_id] = perm_block
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.border_title = "permission required"
                self._append(perm_block)
                select = PermissionSelect(tool_use_id)
                self._append(select)
                logger.debug(
                    "PermissionSelect mounted before #prompt  pending=%d",
                    len(self._pending_permission_blocks),
                )

            elif t == "permission.denied":
                # 处理超时或断连等非用户交互触发的 deny（用户主动 deny 已由 on_permission_select_decided 处理）
                tool_use_id = str(event.get("tool_use_id", ""))
                decision = str(event.get("decision", "denied"))
                if tool_use_id in self._pending_permission_blocks:
                    perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
                    if perm_block is not None:
                        perm_block._resolve(decision)
                    try:
                        select = self.query_one(PermissionSelect)
                        select.remove()
                    except Exception:
                        pass
                    if not self._pending_permission_blocks:
                        p = self._prompt()
                        if p is not None:
                            p.disabled = False
                            p.read_only = False
                            p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                            p.focus()

            elif t == "log.line":
                level = event.get("level", "INFO")
                color = (
                    "bold red"
                    if level == "ERROR"
                    else ("yellow" if level == "WARNING" else "dim")
                )
                self._append(
                    Static(
                        f"[{color}]{level}[/{color}]  "
                        f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                        classes="log-line",
                    )
                )
        except Exception:
            logger.exception(
                "_handle_event crashed  event_type=%s", event.get("type", "?")
            )


def run(config: CrispConfig, replayed_runs_id: str | None = None) -> None:
    """TUI 入口：读取配置并启动 KamaTuiApp"""
    app = CrispTuiApp(config, replayed_runs_id=replayed_runs_id)
    app.run()
