from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentProfile:
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""


class AgentProfileLoader:
    _BUILTIN_DIR = Path(__file__).parent / "builtin"

    def load(self, name: str) -> AgentProfile | None:
        for path in self._search_paths(name):
            if path.exists():
                try:
                    return self._parse(path, name)
                except Exception:
                    return None
        return None

    def _search_paths(self, name: str) -> list[Path]:
        builtin = self._BUILTIN_DIR / f"{name}.toml"
        global_ = Path("~/.crispcode/agents").expanduser() / f"{name}.toml"
        local = Path(".crispcode/agents") / f"{name}.toml"
        return [local, global_, builtin]

    def _parse(self, path: Path, name: str) -> AgentProfile:
        """解析 TOML 角色配置文件"""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        agent = data.get("agent", {})
        return AgentProfile(
            name=name,
            description=agent.get("description", ""),
            system_prompt=agent.get("system_prompt", "").strip(),
            allowed_tools=agent.get("allowed_tools", []),
            model=agent.get("model", "deepseek-v4-falsh"),
        )
