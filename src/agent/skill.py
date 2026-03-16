"""Skill 定义与加载模块。

Skill 文件支持两种格式：
  - Markdown (.md)  : YAML frontmatter + Markdown 正文（正文作为系统提示扩展内容）
  - TOML    (.toml) : Python 3.11+ 内置 tomllib 解析

Skill 文件搜索路径（优先级由高到低）：
  ./skills/           项目级 skills 目录
  ./.agent/skills/    项目隐藏 skills 目录
  ~/.agent/skills/    用户级 skills 目录

也可通过环境变量 SKILL_SEARCH_DIRS 覆盖搜索路径（使用 os.pathsep 分隔，macOS/Linux 为 ':'）。

Skill 文件字段（Markdown frontmatter 或 TOML 顶层）：
  name          : str  - skill 名称（默认取文件名）
  description   : str  - 简短描述
  system_prompt : str  - 系统提示词模板，支持 {placeholder} 占位符
  model         : str  - 覆盖默认模型
    tools         : list - 启用的工具组 ["file_io", "dir_io", "python_exec", "bash_exec", "browser_use"]
  params        : dict - 提示词默认参数（可被 --prompt-param 覆盖）

Markdown 正文 (frontmatter 之后的部分) 会追加到 system_prompt 末尾。
"""

import re
import tomllib
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Dict, List, Optional

from .config import get_config_value

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# 搜索路径
# ---------------------------------------------------------------------------

DEFAULT_SKILL_SEARCH_DIRS: List[Path] = [
    Path("skills"),
    Path(".agent") / "skills",
    Path.home() / ".agent" / "skills",
]


def _resolve_skill_search_dirs() -> List[Path]:
    """解析技能搜索目录，支持通过 SKILL_SEARCH_DIRS 配置。"""
    configured = get_config_value("SKILL_SEARCH_DIRS")
    if not configured:
        return list(DEFAULT_SKILL_SEARCH_DIRS)

    parts = [p.strip() for p in configured.split(':') if p.strip()]
    if not parts:
        return list(DEFAULT_SKILL_SEARCH_DIRS)

    dirs: List[Path] = []
    seen: set[str] = set()
    for part in parts:
        resolved = Path(part).expanduser()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(resolved)

    return dirs or list(DEFAULT_SKILL_SEARCH_DIRS)


def get_skill_search_dirs() -> List[Path]:
    """返回当前生效的技能搜索目录（按当前环境变量动态解析）。"""
    return _resolve_skill_search_dirs()

ALL_TOOL_GROUPS = {"file_io", "dir_io", "python_exec", "bash_exec", "browser_use"}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SkillDefinition:
    name: str
    description: str = ""
    system_prompt: str = ""
    model: Optional[str] = None
    tools: Optional[List[str]] = None  # None = 全部工具组
    params: Dict[str, str] = field(default_factory=dict)
    body: str = ""  # .md 正文（已追加到 system_prompt 之后时使用）


# ---------------------------------------------------------------------------
# Markdown YAML frontmatter 解析
# ---------------------------------------------------------------------------


def _parse_md_frontmatter(text: str) -> tuple[dict, str]:
    """从 Markdown 文本中提取 YAML frontmatter，返回 (dict, body)。"""
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return {}, text

    end_idx = text.find("\n---", 4)
    if end_idx == -1:
        return {}, text

    frontmatter_text = text[4:end_idx]
    body = text[end_idx + 4 :].strip()

    if _HAS_YAML:
        try:
            data = _yaml.safe_load(frontmatter_text) or {}
        except Exception:
            data = {}
    else:
        data = _parse_minimal_yaml(frontmatter_text)

    return data, body


def _parse_minimal_yaml(text: str) -> dict:
    """不依赖 PyYAML 的最小化 YAML 解析器，支持：
    - 顶层 key: value（字符串）
    - 顶层 key: 后接缩进列表（- item）
    - 顶层 key: 后接缩进 key: value 对（嵌套 dict）
    """
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # 跳过空行和注释
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        inline_value = m.group(2).strip().strip("\"'")

        if inline_value:
            result[key] = inline_value
            i += 1
            continue

        # 无行内值：扫描后续缩进行，收集列表或嵌套 dict
        items: List[str] = []
        nested: Dict[str, str] = {}
        i += 1
        while i < len(lines):
            sub = lines[i]
            if sub and not (sub[0] == " " or sub[0] == "\t"):
                break  # 缩进结束
            sub_stripped = sub.lstrip()
            if sub_stripped.startswith("- "):
                items.append(sub_stripped[2:].strip().strip("\"'"))
            else:
                nm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", sub_stripped)
                if nm:
                    nested[nm.group(1)] = nm.group(2).strip().strip("\"'")
            i += 1

        if items:
            result[key] = items
        elif nested:
            result[key] = nested
        # else: 空 block，忽略

    return result


# ---------------------------------------------------------------------------
# 公共加载逻辑
# ---------------------------------------------------------------------------


def _dict_to_skill(data: dict, body: str, default_name: str) -> SkillDefinition:
    """将解析后的 dict 转换为 SkillDefinition。"""
    name = str(data.get("name", default_name))
    description = str(data.get("description", ""))
    system_prompt = str(data.get("system_prompt", ""))
    model: Optional[str] = None
    if "model" in data and data["model"]:
        model = str(data["model"])

    raw_tools = data.get("tools")
    tools: Optional[List[str]] = None
    if raw_tools is not None:
        if isinstance(raw_tools, list):
            tools = [str(t).strip() for t in raw_tools if str(t).strip()]
        elif isinstance(raw_tools, str):
            tools = [t.strip() for t in raw_tools.split(",") if t.strip()]

    raw_params = data.get("params")
    params: Dict[str, str] = {}
    if isinstance(raw_params, dict):
        params = {str(k): str(v) for k, v in raw_params.items()}

    return SkillDefinition(
        name=name,
        description=description,
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        params=params,
        body=body,
    )


def _load_skill_file(path: Path, default_name: Optional[str] = None) -> SkillDefinition:
    """从文件路径加载 SkillDefinition。支持 .md 和 .toml。"""
    # 目录式 skill 常用 <skill_name>/SKILL.md，默认名应为目录名而不是文件名 SKILL。
    resolved_default_name = default_name or (
        path.parent.name if path.name.lower() == "skill.md" else path.stem
    )

    if path.suffix.lower() == ".toml":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return _dict_to_skill(data, body="", default_name=resolved_default_name)

    # .md（默认）
    text = path.read_text(encoding="utf-8")
    data, body = _parse_md_frontmatter(text)
    return _dict_to_skill(data, body=body, default_name=resolved_default_name)


def _candidate_paths(skill_name: str) -> List[Path]:
    """返回给定 skill 名称的所有候选文件路径。"""
    candidates: List[Path] = []
    for d in get_skill_search_dirs():
        candidates.append(d / f"{skill_name}.md")
        candidates.append(d / f"{skill_name}.toml")
        candidates.append(d / skill_name / "SKILL.md")
    return candidates


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def merge_skills(skills: List[SkillDefinition]) -> SkillDefinition:
    """将多个 SkillDefinition 合并为一个，合并规则：

    - name          : 各 skill 名称用 '+' 连接
    - description   : 各描述换行连接
    - system_prompt : 各 system_prompt 用 '\n\n---\n\n' 分隔拼接（跳过空值）
    - body          : 各 body 用 '\n\n' 分隔拼接（跳过空值）
    - model         : 最后一个指定了 model 的 skill 胜出
    - tools         : 取所有已指定 tools 的并集；若所有 skill 均为 None
                      (= 不限制工具)，则合并结果也为 None
    - params        : 后加载的 skill 覆盖同名参数
    """
    if not skills:
        raise ValueError("merge_skills 需要至少一个 skill")
    if len(skills) == 1:
        return skills[0]

    name = "+".join(sk.name for sk in skills)
    description = "\n".join(sk.description for sk in skills if sk.description)

    prompt_parts = [sk.system_prompt for sk in skills if sk.system_prompt]
    system_prompt = "\n\n---\n\n".join(prompt_parts)

    body_parts = [sk.body for sk in skills if sk.body]
    body = "\n\n".join(body_parts)

    model: Optional[str] = None
    for sk in skills:
        if sk.model:
            model = sk.model

    # tools 合并：只要有一个 skill 指定了工具组，就取并集；全部为 None 则保持 None
    any_restricted = any(sk.tools is not None for sk in skills)
    if any_restricted:
        merged_tools: List[str] = []
        seen: set = set()
        for sk in skills:
            for t in (sk.tools or list(ALL_TOOL_GROUPS)):
                if t not in seen:
                    merged_tools.append(t)
                    seen.add(t)
        tools: Optional[List[str]] = merged_tools
    else:
        tools = None

    params: Dict[str, str] = {}
    for sk in skills:
        params.update(sk.params)

    return SkillDefinition(
        name=name,
        description=description,
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        params=params,
        body=body,
    )


def load_skill(skill_name: str) -> SkillDefinition:
    """按名称加载 skill，失败时抛出 FileNotFoundError。"""
    for path in _candidate_paths(skill_name):
        if path.exists():
            return _load_skill_file(path, default_name=skill_name)

    searched = "\n".join(f"  {p}" for p in _candidate_paths(skill_name))
    raise FileNotFoundError(
        f"未找到 skill '{skill_name}'。已搜索以下路径:\n{searched}"
    )


def list_skills() -> List[SkillDefinition]:
    """列出所有搜索路径中的 skill。"""
    found: Dict[str, SkillDefinition] = {}

    for search_dir in get_skill_search_dirs():
        if not search_dir.exists():
            continue
        # *.md / *.toml
        for ext in ("*.md", "*.toml"):
            for p in sorted(search_dir.glob(ext)):
                if p.stem not in found:
                    try:
                        found[p.stem] = _load_skill_file(p, default_name=p.stem)
                    except Exception:
                        pass
        # 子目录内的 SKILL.md
        for subdir in sorted(d for d in search_dir.iterdir() if d.is_dir()):
            skill_file = subdir / "SKILL.md"
            if skill_file.exists() and subdir.name not in found:
                try:
                    found[subdir.name] = _load_skill_file(
                        skill_file, default_name=subdir.name
                    )
                except Exception:
                    pass

    return list(found.values())
