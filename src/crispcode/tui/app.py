from __future__ import annotations

import asyncio
from asyncio import events
import json
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Label, Markdown, RichLog, Static
from textual.widget import Widget
from crispcode.core.config import CrispConfig
from textual.containers import VerticalScroll

from crispcode.core.transport.socket_client import IpcError, SocketClient


def _preview(s: str, n: int) -> str:
    """截断预览文字"""
    return s[:n] + "..." if len(s) > n else s


def _params_str(params: dict[str, Any]) -> str:
    """将字典参数序列化为格式化的JSON字符串"""
    return json.dumps(params, ensure_ascii=False)


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
        yield Static("", classes="detail")

    def _summary(self) -> tuple[str, str]:
        """生成摘要行文本"""
        params_pre = _preview(self._params_full, 30)
        icon = "[bold yellow]✎[/bold yellow]"
        title = f"{icon} [#FFCCFF]{self._tool_name}[/#FFCCFF]"
        hint = "[dim](▸ click to toggle)[/dim]" if len(self._output) > 30 else ""
        title += hint

        line = ""
        if self._finished:
            out_pre = _preview(self._output, 30)
            color = "red" if self._is_error else "dim"

            line += (
                f"\n[{color}]{out_pre}[{color}]"
                f"    [dim]{self._elapsed_ms}ms[/dim]\n"
            )

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
                f"[dim]params:[/dim]\n    {self._params_full}\n"
                f"[dim]output:[/dim]\n\n{self._output}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


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
        self._config = config

    def compose(self) -> ComposeResult:
        """构建 UI：顶部状态栏 + 可滚动事件日志"""
        yield Label("[bold]CrispCode[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")

    def on_mount(self) -> None:
        """挂载后启动 socket 连接 worker"""
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")

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
                header.update(
                    "[bold]CrispCode[/bold] [red]not connected - retrying...[/red]"
                )
                await asyncio.sleep(2)
                continue
            self._client = client
            header.update(
                f"[bold]CrispCode[/bold] [dim]{self._host}:{self._port}[/dim]"
            )
            loop_task = asyncio.create_task(client.run_event_loop())

            async def on_event(event: dict[str, Any]) -> None:
                self._handle_event(event)

            client.on_event(on_event)

            try:
                params: dict[str, Any] = {
                    "topics": [
                        "run.*",
                        "step.*",
                        "tool.*",
                        "llm.token",
                        "llm.usage",
                        "log.*",
                    ],
                    "scope": "global",
                }
                if self._replayed_runs_id is not None:
                    params["replayed_from_run"] = self._replayed_runs_id
                await client.send_command("event.subscribe", params)
                await loop_task
            except IpcError as e:
                header.update(
                    f"[bold]CrispCode[/bold] [red]subscribe error: {e}[[/red]"
                )
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._break_llm()
                await client.close()

            header.update(
                "[bold]CrispCode[/bold]  [dim]disconnected — retrying...[/dim]"
            )
            await asyncio.sleep(2)

    def _handle_event(self, event: dict[str, Any]) -> None:
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

        if t == "run.started":
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
            self._append(
                Static(
                    f"[dim]   tokens  "
                    f"in={event.get('input_tokens')} "
                    f"out={event.get('output_tokens')} "
                    f"cache={event.get('cache_read_input_tokens')}[/dim]",
                    classes="usage",
                )
            )

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


def run(config: CrispConfig, replayed_runs_id: str | None = None) -> None:
    """TUI 入口：读取配置并启动 KamaTuiApp"""
    app = CrispTuiApp(config, replayed_runs_id=replayed_runs_id)
    app.run()
