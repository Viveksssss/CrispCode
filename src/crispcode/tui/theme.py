"""TUI 主题系统：定义配色方案，支持运行时切换。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """一组完整的 TUI 配色方案。"""

    name: str
    display: str

    # ── 基础色 ──
    bg: str  # 最底层背景
    surface: str  # 卡片/区块背景
    surface2: str  # 次级表面（输入框、弹窗）

    # ── 主色 ──
    primary: str  # 标题栏、边框、重点高亮
    accent: str  # 图标、装饰性元素

    # ── 语义色 ──
    success: str
    error: str

    # ── 文字色 ──
    text: str  # 正文
    text_dim: str  # 次要文字
    text_muted: str  # 最淡文字

    # ──────────────────────────────────────────────
    #  CSS 生成
    # ──────────────────────────────────────────────
    def css(self) -> str:
        """生成 CrispTuiApp 所需的完整 CSS 字符串。"""
        return f"""
    Screen {{ background: {self.bg}; }}
    #header {{
        height: 1;
        background: {self.surface2};
        color: {self.text};
        padding: 0 1;
    }}
    #log-view {{
        height: 1fr;
        scrollbar-size: 1 1;
        background: {self.bg};
    }}
    #ctx-bar {{
        height: 1;
        min-height: 1;
        max-height: 1;
        dock: bottom;
        background: {self.surface};
        color: {self.text_dim};
        padding: 0 2;
    }}
    Static.run-id {{ color: {self.primary}; padding: 1 2 0 2; }}
    Static.run-header {{ color: {self.text}; padding: 0 1 0 2; background: {self.surface}; }}
    Static.step-divider {{ color: {self.text_dim}; padding: 0 2; }}
    Static.run-ok {{ color: {self.success}; padding: 1 2 1 1; }}
    Static.run-err {{ color: {self.error}; padding: 0 2 1 2; }}
    Static.usage {{ padding: 0 2;
    color: {self.text_dim}; 
    }}
    Static.log-line {{ padding: 0 2; color: {self.text}; }}
    Static.log-line.thinking {{
        color: {self.text_dim};
        background: {self.surface};
        padding: 1 2;
        border-left: thick {self.accent};
    }}
    #banner {{
        padding-left: 3;
        padding-top: 3;
    }}
    """

    # ──────────────────────────────────────────────
    #  子组件 CSS 片段
    # ──────────────────────────────────────────────
    def css_llm_stream(self) -> str:
        return f"LLMStreamBlock {{ padding: 0 2; color: {self.text}; }}"

    def css_tool_call(self) -> str:
        return f"""
    ToolCallBlock {{ height: auto; padding: 0 2; color: {self.text_dim}; border: {self.primary}; }}
    ToolCallBlock.error {{ height: auto; padding: 0 2; color: {self.text_dim}; border: {self.error}; }}
    ToolCallBlock > .tool_title {{ color: {self.text_dim}; padding-left: 0; }}
    ToolCallBlock > .summary {{ color: {self.text_dim}; padding-left: 4; }}
    ToolCallBlock > .detail {{ display: none; padding: 0 2 0 4; color: {self.text}; }}
    ToolCallBlock.expanded > .detail {{ display: block; padding-left: 4; }}
    """

    def css_chat_area(self) -> str:
        return f"""
    ChatTextArea {{
        height: auto;
        min-height: 3;
        max-height: 12;
        border: round {self.primary};
        background: {self.bg};
        color: {self.text};
        padding: 0 1;
        margin: 1 2;
        scrollbar-size-vertical: 1;
        padding-left: 1;
        padding-right: 1;
    }}
    ChatTextArea:focus {{
        border: round {self.accent};
        background: {self.bg};
    }}
    """

    def css_slash_complete(self) -> str:
        return f"""
    SlashCompleteWidget {{
        height: auto;
        padding: 0 1;
        margin: 0 2;
        background: {self.surface};
        border: round {self.primary};
    }}
    """

    def css_permission_select(self) -> str:
        return f"""
    PermissionSelect {{
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
        background: {self.surface};
        border: tall {self.primary};
    }}
    """

    # ──────────────────────────────────────────────
    #  Banner
    # ──────────────────────────────────────────────
    def banner(self) -> str:
        """生成带主题色的 ASCII art banner。"""
        c1 = self.primary
        c2 = self.accent
        dt = self.text_dim
        return (
            f"[bold {c1}] ██████╗ ██████╗ ██╗███████╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗[/bold {c1}]\n"
            f"[bold {c2}]██╔════╝██╔═══██╗██║██╔════╝██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝[/bold {c2}]\n"
            f"[bold {c1}]██║     ██║   ██║██║███████╗██████╔╝██║     ██║   ██║██████╔╝█████╗  [/bold {c1}]\n"
            f"[bold {c2}]██║     ██║   ██║██║╚════██║██╔═══╝ ██║     ██║   ██║██╔══██╗██╔══╝  [/bold {c2}]\n"
            f"[bold {c1}]╚██████╗╚██████╔╝██║███████║██║     ╚██████╗╚██████╔╝██║  ██║███████╗[/bold {c1}]\n"
            f"[bold {c2}] ╚═════╝ ╚═════╝ ╚═╝╚══════╝╚═╝      ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝[/bold {c2}]\n"
            f"[{dt}]  输入消息开始对话  ·  键入 / 触发 skill  ·  Ctrl+C 退出  ·  /theme 切换主题[/{dt}]"
        )


# ──────────────────────────────────────────────────
#  预设主题
# ──────────────────────────────────────────────────

BLUE_PINK = Theme(
    name="blue_pink",
    display="蓝粉",
    bg="#0D1B2A",
    surface="#162447",
    surface2="#1B2A4A",
    primary="#4A6CF7",
    accent="#FF6B9D",
    success="green",
    error="red",
    text="#E8E8F0",
    text_dim="#B8B5FF",
    text_muted="#6B6B8D",
)

DRACULA = Theme(
    name="dracula",
    display="暗紫",
    bg="#282A36",
    surface="#343746",
    surface2="#44475A",
    primary="#BD93F9",
    accent="#FF79C6",
    success="green",
    error="red",
    text="#F8F8F2",
    text_dim="#8BE9FD",
    text_muted="#6272A4",
)

NORD = Theme(
    name="nord",
    display="极光",
    bg="#2E3440",
    surface="#3B4252",
    surface2="#434C5E",
    primary="#88C0D0",
    accent="#B48EAD",
    success="green",
    error="red",
    text="#ECEFF4",
    text_dim="#81A1C1",
    text_muted="#4C566A",
)

CYBERPUNK = Theme(
    name="cyberpunk",
    display="赛博",
    bg="#0A0A0F",
    surface="#12121A",
    surface2="#1A1A2E",
    primary="#00FF9F",
    accent="#FF0080",
    success="green",
    error="red",
    text="#E0E0E0",
    text_dim="#00BFFF",
    text_muted="#555577",
)

SOLARIZED = Theme(
    name="solarized",
    display="日晒",
    bg="#002B36",
    surface="#073642",
    surface2="#094959",
    primary="#268BD2",
    accent="#D33682",
    success="green",
    error="red",
    text="#FDF6E3",
    text_dim="#93A1A1",
    text_muted="#586E75",
)

# ── 主题注册表（有序） ──
THEMES: list[Theme] = [BLUE_PINK, DRACULA, NORD, CYBERPUNK, SOLARIZED]

THEME_BY_NAME: dict[str, Theme] = {t.name: t for t in THEMES}
