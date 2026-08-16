from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Skill:
    name: str
    description: str
    system_prompt_template: str
    allowed_tools: list[str] = field(default_factory=list)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_file(path: Path) -> Skill:
    """
    _FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    """
    text = path.read_text(encoding="utf-8")

    m = _FRONTMATTER_RE.match(text)
    if not m:
        return Skill(
            name=path.stem,
            description="",
            allowed_tools=[],
            prompt=text.strip(),
        )

    front = m.group(1)
    body = text[m.end() :].lstrip("\n")

    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError:
        data = {}
    return Skill(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        allowed_tools=data.get("allowed_tools", []),
        prompt=body.strip(),
    )


class SkillLoader:
    """按三级优先级（项目本地 > 用户全局 > 内建）查找并解析 skill"""

    _BUILTIN_DIR = Path(__file__) / "builtin"

    def resolve(self, name: str) -> Skill | None:
        """按优先级查找 skill 文件；未找到返回 None"""
        for path in self._search_paths(name):
            if path.exists():
                try:
                    return _parse_skill_file(path)
                except Exception:
                    return None
        return None

    def _search_paths(self, name: str) -> list[Path]:
        """返回候选路径列表，同时支持扁平文件（name.md）和目录式（name/SKILL.md）两种格式"""
        dirs = [
            Path(".crispcode/skills"),
            Path("~/.crispcode/skills").expanduser(),
            self._BUILTIN_DIR,
        ]

        paths: list = []
        for d in dirs:
            paths.append(d / f"{name}.md")
            paths.append(d / name / "SKILL.md")

        return paths

    def list_all(self) -> list[str]:
        """列出所有可用 skill 名称（内建 + 用户全局 + 项目本地，去重后以项目本地覆盖为准）"""

        skills: dict[str, Path] = {}

        # 从低到高优先级
        for directory in [
            self._BUILTIN_DIR,
            Path("~/.crispcode/skills").expanduser(),
            Path(".crispcode/skills"),
        ]:
            if not directory.exists():
                continue

            # 单文件 skill: <skill_name>.md
            for f in directory.glob("*.md"):
                skills[f.stem] = f

            # 目录型 skill: <skill_name>/SKILL.md
            for f in directory.glob("*/SKILL.md"):
                skills[f.parent.name] = f

        # 按名称排序返回
        return sorted(skills.keys())

    def list_all_skills(self) -> list[Skill]:
        """列出所有可用 Skill 对象（含描述），项目本地覆盖同名内建"""
        seen: dict[str, Skill] = {}
        for d in [
            self._BUILTIN_DIR,
            Path("~/.crispcode/skills").expanduser(),
            Path(".crispcode/skills"),
        ]:
            if d.exists():
                for f in sorted(d.glob("*.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
                for f in sorted(d.glob("*/SKILL.md")):
                    try:
                        skill = _parse_skill_file(f)
                        seen[skill.name] = skill
                    except Exception:
                        pass
        return list(seen.values)

    def render_prompt(self, skill: Skill, arguments: str) -> str:
        return skill.system_prompt_template.replace("$ARGUMENTS", arguments)
