from __future__ import annotations

import asyncio
import json
import logging
import time
from asyncio import events
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.markup import escape
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Markdown, RichLog, Static, TextArea

from crispcode.core.config import CrispConfig
from crispcode.core.skills.loader import SkillLoader
from crispcode.core.transport.socket_client import IpcError, SocketClient
from crispcode.tui.theme import THEMES, Theme

logger = logging.getLogger(__name__)


def _preview(s: str, n: int) -> str:
    """截断预览文字"""
    return s[:n] + "..." if len(s) > n else s


def _params_str(params: dict[str, Any]) -> str:
    """将字典参数序列化为格式化的JSON字符串"""
    return json.dumps(params, ensure_ascii=False, indent=2)


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

    DEFAULT_CSS = """
    LLMStreamBlock { 
        padding-top: 1;
        padding-left: 2;
        padding-right: 2;
        color: #E8E8F0; 
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._text = ""
        self._finalized = False

    def append_token(self, token: str) -> None:
        if self._finalized:
            return
        self._text += token
        self.update(self._text)

    def finalize_markdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._text.strip() and self.is_attached:
            self.mount(Markdown(self._text))


class ThinkingStreamBlock(Static):
    """可折叠的 thinking 流式块：逐 token 累积，结束后可点击展开/收起全部文本。"""

    DEFAULT_CSS = """
    ThinkingStreamBlock {
        height: auto;
        padding: 0 2;
        color: #B8B5FF;
        border-left: tall #4A6CF7;
        background: #1E1E2E;
    }
    ThinkingStreamBlock > .th_title {
        color: #B8B5FF;
        padding-left: 0;
    }
    ThinkingStreamBlock > .th_preview {
        color: #6E6E8A;
        padding-left: 4;
    }
    ThinkingStreamBlock > .th_body {
        display: none;
        padding: 0 2 0 4;
        color: #B8B5FF;
    }
    ThinkingStreamBlock.expanded > .th_preview {
        display: none;
    }
    ThinkingStreamBlock.expanded > .th_body {
        display: block;
        padding-left: 4;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._text = ""
        self._finalized = False

    def compose(self) -> ComposeResult:
        yield Static("🧠 thinking...", classes="th_title")
        yield Static("", classes="th_preview")
        yield Markdown("", classes="th_body")

    def append_token(self, token: str) -> None:
        if self._finalized:
            return
        self._text += token
        if self.is_attached:
            try:
                preview = self._text[-60:] if len(self._text) > 60 else self._text
                self.query_one(".th_preview", Static).update(
                    f"[dim]{escape(preview)}[/dim]"
                )
            except NoMatches:
                pass

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self.is_attached:
            try:
                title = self.query_one(".th_title", Static)
                title.update(f"🧠 thinking  [dim](▸ click)[/dim]")
                self.query_one(".th_body", Markdown).update(self._text)
            except NoMatches:
                pass

    def on_click(self) -> None:
        if not self._finalized:
            return
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            self.add_class("expanded")


class ToolCallBlock(Static):
    """可折叠的工具调用块: 折叠式显示摘要,点击后显示完整的params和output"""

    DEFAULT_CSS = """
    ToolCallBlock { height: auto; padding: 0 2; color: #B8B5FF; border: #4A6CF7; }
    ToolCallBlock.error { height: auto; padding: 0 2; color: #B8B5FF; border: #FF4757; }
    ToolCallBlock > .tool_title { color: #B8B5FF; padding-left: 0; }
    ToolCallBlock > .summary { color: #B8B5FF; padding-left: 4; }
    ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: #E8E8F0; }
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

    def _get_theme(self) -> Theme:
        """安全获取当前主题，widget 未挂载时回退到默认主题"""
        try:
            return self.app._theme
        except Exception:
            return THEMES[0]

    def _summary(self) -> tuple[str, str]:
        """生成摘要行文本"""
        t = self._get_theme()
        params_pre = _preview(self._params_full, 30)
        icon = f"[bold {t.accent}]✎[/bold {t.accent}]"
        title = f"{icon} [{t.accent}]{self._tool_name}[/{t.accent}]"
        hint = "[dim](▸ click to toggle)[/dim]" if len(self._output) > 30 else ""
        title += hint

        line = f"[dim]{params_pre}[/dim]"
        if self._finished:
            color = t.error if self._is_error else t.success
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
            from rich.text import Text
            detail_text = Text()
            detail_text.append("params:\n", style="dim")
            detail_text.append(self._params_full + "\n")
            detail_text.append("output:\n\n", style="dim")
            detail_text.append(self._output + "\n")
            detail_text.append("elapsed: ", style="dim")
            detail_text.append(f"{self._elapsed_ms}ms", style="dim")
            detail.update(detail_text)

            self.add_class("expanded")


class PermissionSelect(Static):
    """内联权限选择控件：挂载在日志流中，键盘焦点无需 ModalScreen。"""

    can_focus = True

    DEFAULT_CSS = """
    PermissionSelect{
        height:auto;
        padding: 0 2;
        margin-bottom: 1;
        background: #162447;
        border: tall #4A6CF7;
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
        try:
            t = self.app._theme
        except Exception:
            t = THEMES[0]
        lines: list[str] = []
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(
                    f"  [bold {t.accent}]❯ {label}[/bold {t.accent}]  [{t.text_dim}]{key_hint}[/{t.text_dim}]"
                )
            else:
                lines.append(
                    f"    [{t.text}]{label}[/{t.text}]  [{t.text_dim}]{key_hint}[/{t.text_dim}]"
                )
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
    def __init__(
        self,
        tool_use_id: str,
        tool_name: str,
        param_preview: str,
        theme: Theme | None = None,
    ) -> None:
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._param_preview = param_preview
        self._resolved = False
        self._init_theme = theme or THEMES[0]
        super().__init__(self._pending_text(), classes="log-line")

    def _get_theme(self) -> Theme:
        """安全获取当前主题，widget 未挂载时回退到初始化时传入的主题"""
        try:
            return self.app._theme
        except Exception:
            return self._init_theme

    def _pending_text(self) -> str:
        t = self._get_theme()
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        return f"[bold {t.accent}]? permission[/bold {t.accent}]  [bold {t.text}]{self._tool_name}[/bold {t.text}]{preview}"

    # 将块收缩为单行摘要并发布 Resolved 消息
    def _resolve(self, decision: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        t = self._get_theme()
        allowed = decision in ("allow_once", "always_allow")
        icon = (
            f"[bold {t.success}]✓[/bold {t.success}]"
            if allowed
            else f"[bold {t.error}]✗[/bold {t.error}]"
        )
        label = self._LABEL_MAP.get(decision, decision)
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        self.update(
            f"{icon} permission  [bold {t.text}]{self._tool_name}[/bold {t.text}]{preview}  [dim]{label}[/dim]"
        )
        self.post_message(self.Resolved(self, decision))


class SlashCompleteWidget(Static):
    """斜杠命令自动补全弹出框：输入 / 时显示可用 skill 列表并支持键盘筛选与选择。"""

    can_focus = False

    DEFAULT_CSS = """
    SlashCompleteWidget{
        height: auto;
        padding: 0 1;
        margin: 0 2;
        background: #162447;
        border: round #4A6CF7;
    }
    """

    class Selected(Message):
        """用户选中某条命令时发布"""

        def __init__(self, skill_name: str) -> None:
            """初始化，携带被选中的 skill 名称"""
            self._skill_name = skill_name
            super().__init__()

    def __init__(self, items: list[tuple[str, str]]) -> None:
        """初始化，接收全量 (name, description) 列表"""
        super().__init__("")
        self._all_items = items
        self._filtered: list[tuple[str, str]] = list(items)
        self._cursor = 0

    def set_query(self, query: str) -> None:
        """根据查询字符串筛选列表，重置光标并重新渲染"""
        q = query.lower()
        self._filtered = [(n, d) for n, d in self._all_items if not q or q in n.lower()]
        self._cursor = min(self._cursor, max(0, len(self._filtered) - 1))
        if self.is_attached:
            self._redraw()

    def _redraw(self) -> None:
        """渲染筛选后的命令列表，高亮当前光标项"""
        try:
            t = self.app._theme
        except Exception:
            t = THEMES[0]
        if not self._filtered:
            self.update("[dim]  no matching commands[/dim]")
            return
        lines: list[str] = []
        for i, (name, desc) in enumerate(self._filtered):
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            if i == self._cursor:
                lines.append(
                    f" [bold {t.accent}]> /{name}[/bold {t.accent}]{desc_part}"
                )
            else:
                lines.append(f"    [{t.primary}]/{name}[/{t.primary}]{desc_part}")

        lines.append("[dim]  ↑↓ navigate   tab/enter select   esc dismiss[/dim]")
        self.update("\n".join(lines))

    def move_up(self) -> None:
        if self._filtered:
            self._cursor = (self._cursor - 1) % len(self._filtered)
            self._redraw()

    def move_down(self) -> None:
        if self._filtered:
            self._cursor = (self._cursor + 1) % len(self._filtered)
            self._redraw()

    def select_current(self) -> None:
        if self._filtered:
            self.post_message(self.Selected(self._filtered[self._cursor][0]))

    def has_selection(self) -> None:
        return len(self._filtered) > 0

    def on_mount(self) -> None:
        self._redraw()


class ChatTextArea(TextArea):
    """支持Enter提交,Cmd/Shift/+Enter换行"""

    DEFAULT_CSS = """
    ChatTextArea {
        height: auto;
        min-height: 3;
        max-height: 12;
        border: round #4A6CF7;
        background: #0D1B2A;
        color: #E8E8F0;
        padding: 0 1;
        margin: 1 2;
        scrollbar-size-vertical: 1;
        padding-left:1;
        padding-right:1;
    }
    ChatTextArea:focus {
        border: round #FF6B9D;
        background: #0D1B2A;
    }
    """

    class Submitted(Message):
        def __init__(self, area: ChatTextArea) -> None:
            self.text_area = area
            self.value = area.text
            super().__init__()

    class SlashChanged(Message):
        def __init__(self, query: str | None) -> None:
            self.query = query
            super().__init__()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/") and " " not in text:
            self.post_message(ChatTextArea.SlashChanged(query=text[1:]))
        else:
            self.post_message(ChatTextArea.SlashChanged(query=None))

    async def _on_key(self, event: events.Key) -> None:
        """Enter 提交；Cmd/Shift/Alt+Enter 插入换行；其余键交回 TextArea 默认行为"""
        key = event.key

        popup: SlashCompleteWidget | None = None
        try:
            popup = self.app.query_one(SlashCompleteWidget)
        except NoMatches:
            popup = None

        if key == "enter":
            event.stop()
            event.prevent_default()
            if popup is not None and popup.has_selection():
                # 如果输入文本精确匹配某个命令，直接提交而非仅选中
                typed = self.text.strip().lstrip("/")
                exact = any(name == typed for name, _ in popup._filtered)
                popup.select_current()
                if exact:
                    # 延迟一帧提交，让 select_current 先填入文本
                    self.app.call_after_refresh(
                        lambda: self.post_message(self.Submitted(self))
                    )
                return
            if self.text.strip():
                self.post_message(self.Submitted(self))

        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return
        if popup is not None:
            if key == "up":
                event.stop()
                event.prevent_default()
                popup.move_up()
                return
            elif key == "down":
                event.stop()
                event.prevent_default()
                popup.move_down()
                return
            elif key == "tab":
                event.stop()
                event.prevent_default()
                popup.select_current()
                return
            elif key == "escape":
                event.stop()
                event.prevent_default()
                self.post_message(ChatTextArea.SlashChanged(query=None))
                return

        await super()._on_key(event)


class CrispTuiApp(App[None]):
    """CrispCode 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    TITLE = "CrispCode TUI"
    BINDINGS = [Binding("q", "quit", "Quit")]

    # ── 运行时由 __init__ 根据主题动态生成 ──
    CSS = THEMES[0].css()

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
        self._current_thinking: ThinkingStreamBlock | None = None
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}
        self._session_id: str | None = None
        self._config = config
        self._busy = False
        self._pending_permission_blocks: dict[str, PermissionBlock] = {}
        self._last_context_pct: float = 0.0
        self._slash_items: list[tuple[str, str]] = []
        self._subagent_runs_ids: dict[str, str] = {}
        self._subagent_start_times: dict[str, float] = {}
        # B1：thinking 与正文分 step 排序——记录当前 run/step 及其正文块，把迟到的 thinking 插到正文之前
        self._cur_runs_id: str = ""
        self._cur_step: int = 0
        self._step_bodies: dict[tuple[str, int], LLMStreamBlock] = {}
        self._pending_thinking: dict[tuple[str, int], list[Widget]] = {}
        self._llm_model: str = self._config.config.CRISP_LLM_DEFAULT_MODEL
        # ── 主题系统 ──
        self._theme_idx: int = 0
        self._theme: Theme = THEMES[0]

    def compose(self) -> ComposeResult:
        """构建 UI：顶部状态栏 + 可滚动事件日志 + 底部常驻 context 条"""
        yield Label("[bold]CrispCode[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")
        yield Static("                     ", id="ctx-bar")
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def on_mount(self) -> None:
        """挂载后启动 socket 连接 worker"""
        self._slash_items = self._build_slash_items()
        self._append(Static(self._theme.banner(), id="banner"))
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."
        self._update_ctx_bar()

    def _build_slash_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = [
            ("compact", "compress context window"),
            ("theme", "切换主题 / cycle theme"),
        ]

        try:
            loader = SkillLoader()
            for skill in loader.list_all_skills():
                desc = skill.description.splitlines()[0] if skill.description else ""
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                items.append((skill.name, desc))
        except Exception:
            pass
        return items

    def on_chat_text_area_slash_changed(self, event: ChatTextArea.SlashChanged) -> None:
        """根据 / 前缀查询字符串挂载、更新或移除自动补全弹窗"""
        query = event.query
        if query is None:
            try:
                self.query_one(SlashCompleteWidget).remove()
            except NoMatches:
                pass
            return
        try:
            popup = self.query_one(SlashCompleteWidget)
            popup.set_query(query)
        except NoMatches:
            popup = SlashCompleteWidget(self._slash_items)
            self.mount(popup, before="#prompt")
            popup.set_query(query)

    def on_slash_complete_widget_selected(
        self, event: SlashCompleteWidget.Selected
    ) -> None:
        prompt = self._prompt()
        if prompt is not None:
            prompt.text = f"/{event._skill_name}"
            prompt.move_cursor(prompt.document.end)
        try:
            self.query_one(SlashCompleteWidget).remove()
        except NoMatches:
            pass

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
                    Static(
                        f"[{self._theme.accent}]warning: failed to close session[/{self._theme.accent}]"
                    )
                )
        self.exit()

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """将输入框提交内容发送给当前 chat session"""
        content = event.value.strip()
        if not content:
            return

        if content == "/compact":
            event.text_area.text = ""
            if (
                self._client is not None
                and self._session_id is not None
                and not self._busy
            ):
                self.run_worker(self._do_compact(), name="compact", exclusive=False)
            return

        if content == "/theme":
            event.text_area.text = ""
            self._cycle_theme()
            return

        if self._client is None or self._session_id is None or self._busy:
            self._append(
                Static(
                    f"[{self._theme.accent}]agent busy or disconnected[/{self._theme.accent}]"
                ),
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

    async def _do_compact(self) -> None:
        if self._client is None or self._session_id is None:
            return
        self._append(
            Static(
                f"[{self._theme.text_dim}]⚡ compacting context...[/{self._theme.text_dim}]",
                classes="log-line",
            )
        )
        try:
            result = await self._client.send_command(
                "session.compact", {"session_id": self._session_id, "focus": ""}
            )
            summary_tokens = result.get("summary_tokens", 0)
            saved_tokens = result.get("saved_tokens", 0)
            self._last_context_pct = 0.0
            self._update_ctx_bar()
            self._append(
                Static(
                    f"[bold {self._theme.primary}]⚡ Context compacted[/bold {self._theme.primary}]"
                    f"  [dim]summary={summary_tokens} tokens  saved≈{saved_tokens} tokens[/dim]",
                    classes="log-line",
                )
            )
        except (IpcError, RuntimeError, OSError) as e:
            self._append(
                Static(
                    f"[{self._theme.error}]compact error: {e}[/{self._theme.error}]",
                    classes="log-line",
                )
            )

    def _cycle_theme(self) -> None:
        """循环切换到下一个主题并刷新 UI"""
        self._theme_idx = (self._theme_idx + 1) % len(THEMES)
        self._theme = THEMES[self._theme_idx]
        t = self._theme
        # 重新生成 CSS
        CrispTuiApp.CSS = t.css()
        self._refresh_theming()
        self._append(
            Static(
                f"[bold {t.primary}]🎨 主题已切换[/bold {t.primary}]"
                f"  [{t.accent}]{t.display}[/{t.accent}]"
                f"  [dim]({t.name})[/dim]",
                classes="log-line",
            )
        )

    def _refresh_theming(self) -> None:
        """将新主题的 CSS 重新应用到当前运行的 App"""
        t = self._theme
        # 更新 App 级 CSS
        CrispTuiApp.CSS = t.css()
        # 更新子组件 DEFAULT_CSS
        LLMStreamBlock.DEFAULT_CSS = t.css_llm_stream()
        ToolCallBlock.DEFAULT_CSS = t.css_tool_call()
        ChatTextArea.DEFAULT_CSS = t.css_chat_area()
        SlashCompleteWidget.DEFAULT_CSS = t.css_slash_complete()
        PermissionSelect.DEFAULT_CSS = t.css_permission_select()
        # 让 Textual 重新解析并应用 CSS
        try:
            self._invalidate_css()
            self.refresh_css()
        except Exception:
            pass
        # 刷新 banner
        try:
            banner = self.query_one("#banner", Static)
            banner.update(self._theme.banner())
        except Exception:
            pass

    def _prompt(self) -> ChatTextArea | None:
        """安全获取输入框，便于组件测试中未挂载时跳过 UI 操作"""
        try:
            return self.query_one("#prompt", ChatTextArea)
        except NoMatches:
            return None

    def _render_ctx_bar(self, pct: float) -> str:
        """生成 context 占用率的彩色进度条字符串"""
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        label = f"ctx:{pct * 100:.1f}%"
        if pct >= 0.85:
            color = "bold red"
        elif pct >= 0.70:
            color = "yellow"
        else:
            color = "dim"
        return f"[{color}]{label} {bar}[/{color}]\t[{self._theme.primary}]{self._llm_model}[/{self._theme.primary}]"

    def _update_ctx_bar(self) -> None:
        """更新常驻在底部的 context 状态条（#ctx-bar）；未挂载时安全跳过"""
        try:
            bar = self.query_one("#ctx-bar", Static)
        except NoMatches:
            return
        bar.update(self._render_ctx_bar(self._last_context_pct))

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
            self._append(
                Static(
                    f"[{self._theme.error}]send error: {e}[/{self._theme.error}]",
                    classes="log-line",
                )
            )

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

    def _insert_before_body(self, key: tuple[str, int], widget: Widget) -> bool:
        """B1：把 widget 插入到指定 (run,step) 的正文块之前；成功返回 True"""
        body = self._step_bodies.get(key)
        if body is None:
            return False
        try:
            log_view = self.query_one("#log-view", VerticalScroll)
            log_view.mount(widget, before=body)
            log_view.scroll_end(animate=False)
            return True
        except Exception:
            logger.exception("failed to insert thinking before body key=%r", key)
            return False

    def _flush_pending_thinking(self, runs_id: str, step: int) -> None:
        """B1：把 (run,step) 缓存的 thinking 块落位到该步正文之前"""
        key = (runs_id, step)
        pending = self._pending_thinking.pop(key, [])
        for tw in pending:
            if not self._insert_before_body(key, tw):
                self._append(tw)

    def _break_llm(self) -> None:
        """结束当前 LLM 流式块（下一个 token 将开启新块）"""
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()  # ✅ 先 finalize
            self._current_llm = None

    def _break_thinking(self) -> None:
        """结束当前 thinking 流式块"""
        if self._current_thinking is not None:
            self._current_thinking.finalize()
            self._current_thinking = None

    def _mount_permission_select(self, select: PermissionBlock) -> None:
        self.mount(select, before="#prompt")

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
                        "llm.thinking.token",
                        "log.*",
                        "session.*",
                        "skill.*",
                        "subagent.*",
                    ],
                    "scope": "global",
                }
                if self._replayed_runs_id is not None:
                    params["replayed_from_run"] = self._replayed_runs_id
                subscribe_result = await client.send_command("event.subscribe", params)
                # 重放时：用对应 session 已持久化的 context_pct 初始化常驻 ctx 条
                if self._replayed_runs_id is not None:
                    self._last_context_pct = float(
                        subscribe_result.get("context_pct") or 0.0
                    )
                    self._update_ctx_bar()
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
                self._break_thinking()  # thinking 结束，正文开始
                token = event.get("token", "")
                if self._current_llm is None:
                    llm_block = LLMStreamBlock()
                    self._append(llm_block)
                    self._current_llm = llm_block
                    self._step_bodies[(self._cur_runs_id, self._cur_step)] = llm_block
                self._current_llm.append_token(token)
                return

            if t == "llm.thinking.token":
                runs_id = event.get("runs_id", "")
                if runs_id not in self._subagent_runs_ids:
                    self._break_llm()  # 先结束正在渲染的正文流
                    token = event.get("token", "")
                    if self._current_thinking is None:
                        th_block = ThinkingStreamBlock()
                        self._append(th_block)
                        self._current_thinking = th_block
                    self._current_thinking.append_token(token)
                return

            self._break_llm()
            self._break_thinking()
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
                t = self._theme
                self._append(
                    Static(
                        f"[bold {t.primary}]▶ run[/bold {t.primary}]  [dim]{runs_id}[/dim]",
                        classes="run-id",
                    )
                )
                self._append(
                    Static(
                        f"[{t.accent}]✴[/{t.accent}] [{t.text}]{goal}[/{t.text}]",
                        classes="run-header",
                    )
                )

            elif t == "step.started":
                step = event.get("step", "")
                self._cur_step = int(step or 0)
                self._cur_runs_id = event.get("runs_id", "")
                # 新 step 开始，先把上一个 step 可能残留的 thinking 落位（避免丢失）
                for key in list(self._pending_thinking.keys()):
                    self._flush_pending_thinking(key[0], key[1])
                t = self._theme
                self._append(
                    Static(
                        f"[{t.primary}]۞[/{t.primary}] [{t.text_dim}]step {step}[/{t.text_dim}]",
                        classes="step-divider",
                    )
                )
            elif t == "skill.invoked":
                skill_name = event.get("skill_name", "")
                arguments = event.get("arguments", "")
                args_preview = _preview(arguments, 80) if arguments else ""
                args_part = f"  [dim]{args_preview}[/dim]" if args_preview else ""
                self._append(
                    Static(
                        f"[bold {self._theme.primary}]/{skill_name}[/bold {self._theme.primary}]{args_part}",
                        classes="log-line",
                    )
                )

            elif t == "subagent.started":
                runs_id = event.get("runs_id", "")
                description = event.get("description", "")
                self._subagent_runs_ids[runs_id] = description
                self._subagent_start_times[runs_id] = time.monotonic()
                short_id = runs_id[:8] if len(runs_id) >= 8 else runs_id
                self._append(
                    Static(
                        f"[dim]┌─[/dim] [{self._theme.text_dim}]{_preview(description, 72)}[/{self._theme.text_dim}]  [dim]{short_id}[/dim]",
                        classes="log-line",
                    )
                )

            elif t == "subagent.finished":
                runs_id = event.get("runs_id", "")
                status = event.get("status", "")
                description = self._subagent_runs_ids.pop(
                    runs_id, event.get("description", "")
                )
                start = self._subagent_start_times.pop(runs_id, None)
                elapsed = (
                    f"  [dim]{time.monotonic() - start:.1f}s[/dim]"
                    if start is not None
                    else ""
                )
                desc_part = f"[{self._theme.text_dim}]{_preview(description, 72)}[/{self._theme.text_dim}]{elapsed}"
                t = self._theme
                if status == "success":
                    self._append(
                        Static(
                            f"[dim]└─[/dim] [bold {t.success}]✓[/bold {t.success}] {desc_part}",
                            classes="log-line",
                        )
                    )
                else:
                    self._append(
                        Static(
                            f"[dim]└─[/dim] [bold {t.error}]✗[/bold {t.error}] {desc_part}",
                            classes="log-line",
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
                runs_id = event.get("runs_id", "")
                # 落位该 run 可能残留的 thinking，并清理跨 run 缓存
                for key in list(self._pending_thinking.keys()):
                    if key[0] == runs_id:
                        self._flush_pending_thinking(key[0], key[1])
                self._step_bodies = {
                    k: v for k, v in self._step_bodies.items() if k[0] != runs_id
                }
                self._pending_thinking = {
                    k: v for k, v in self._pending_thinking.items() if k[0] != runs_id
                }
                self._current_llm = None
                t = self._theme
                if status == "success":
                    self._append(
                        Static(
                            f"[bold {t.success}]✓ completed[/bold {t.success}]  [dim]{steps} steps[/dim]",
                            classes="run-ok",
                        )
                    )
                else:
                    detail = f"  [dim]{reason}[/dim]" if reason else ""
                    self._append(
                        Static(
                            f"[bold {t.error}]✗ failed[/bold {t.error}]{detail}  [dim]{steps} steps[/dim]",
                            classes="run-err",
                        )
                    )

                self._append(
                    Static(
                        f"[{t.surface2}]{'─' * (self.size.width - 10)}[/{t.surface2}]"
                    )
                )
            elif t == "context.compacted":
                orig = event.get("original_tokens", 0)
                summary = event.get("summary_tokens", 0)
                self._last_context_pct = 0.0
                self._update_ctx_bar()
                self._append(
                    Static(
                        f"[bold {self._theme.primary}]⚡ Context compacted[/bold {self._theme.primary}]"
                        f"  [dim]original≈{orig} tokens → summary={summary} tokens[/dim]",
                        classes="log-line",
                    )
                )
            elif t == "llm.usage":
                runs_id = event.get("runs_id", "")
                pct = float(event.get("context_pct") or 0.0)
                self._last_context_pct = pct
                # 常驻底部 context 条：每次 usage 都更新它，不新增日志行
                self._update_ctx_bar()
                # subagent 的 usage 不追加 tokens 日志行，只在常驻条上体现
                if (
                    runs_id not in self._subagent_runs_ids
                    and self._config.tui_config.tokens_enabled
                ):
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
                perm_block = PermissionBlock(
                    tool_use_id, tool_name, param_preview, theme=self._theme
                )
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
    """TUI 入口：读取配置并启动 CrispTuiApp"""
    app = CrispTuiApp(config, replayed_runs_id=replayed_runs_id)
    app.run()
