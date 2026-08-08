from __future__ import annotations
import tomllib
from pathlib import Path

_DEFAULT_POLICY_PATH = Path("~/.crispcode/policy.toml")


def load_policy_file(path: Path | None = None) -> dict[str, str]:
    """加载 policy.toml 中 [always] 节，返回 {tool_name: "allow"/"deny"}；文件不存在时返回空字典"""
    p = (path or _DEFAULT_POLICY_PATH).expanduser()

    if not p.exists():
        return {}

    with p.open("rb") as f:  # tomllib 要求二进制模式
        config = tomllib.load(f)

    always_section = config.get("always", {})
    return {k: v for k, v in always_section.items() if v in ("allow", "deny")}


def save_policy_file(always: dict[str, str], path: Path | None = None) -> None:
    """将 {tool_name: "allow"/"deny"} 写入 policy.toml，覆盖 [always] 节"""
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ~/.crispcode/policy.toml",
        "# 由 crisp-core 自动管理，手动编辑生效但格式须正确",
        "",
        "[always]",
    ]

    for tool, decision in sorted(always.items()):
        lines.append(f'{tool} = "{decision}"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
