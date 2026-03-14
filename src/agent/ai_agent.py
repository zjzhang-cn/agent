import argparse
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .bash_exec import BASH_EXEC_TOOLS, dispatch_bash_exec_tool
from .browser_use import BROWSER_USE_TOOLS, dispatch_browser_use_tool
from .config import (
    get_config_value,
    load_environment,
    parse_bool,
    parse_config_value,
    parse_positive_int,
    parse_string_list,
)
from .dir_io import DIR_IO_TOOLS, dispatch_dir_io_tool
from .file_io import FILE_IO_TOOLS, dispatch_file_io_tool
from .python_exec import PYTHON_EXEC_TOOLS, dispatch_python_exec_tool
from .skill import (
    SkillDefinition,
    get_skill_search_dirs,
    list_skills,
    load_skill,
    merge_skills,
)
from .streaming import (
    consume_stream_with_tool_calls,
    extract_think_content,
    parse_tool_arguments,
)

_DEFAULT_TOOL_GROUP_ORDER = [
    "file_io",
    "dir_io",
    "python_exec",
    "bash_exec",
    "browser_use",
]
_DEFAULT_TOOL_GUIDANCE = {
    "file_io": "- 文件工具：可以读取、写入、编辑、追加文本文件。修改前优先读取相关文件，变更应保持最小且避免误改无关内容。",
    "dir_io": "- 目录工具：可以列出、创建、删除、移动、复制目录，以及检查目录是否存在。执行前先确认路径和影响范围。",
    "python_exec": "- Python 工具：可以执行 Python 脚本或代码片段。适合做逻辑验证、生成结果、复现问题；只有在确实需要验证时才执行。",
    "bash_exec": "- Bash 工具：可以执行 Shell 命令。适合检查环境、搜索项目、运行构建或测试命令；命令应尽量具体且可控。",
    "browser_use": (
        "- 浏览器工具：通过 Playwright 执行网页自动化（页面打开、交互、截图、快照、网络与控制台观察）。"
        "推荐流程：start -> open/navigate -> snapshot -> 使用 ref 执行 click/type/hover/select_option -> 必要时 screenshot/pdf -> stop。"
    ),
}


def _resolve_enabled_tools(enabled_tools: Optional[List[str]]) -> List[str]:
    if enabled_tools is None:
        return list(_DEFAULT_TOOL_GROUP_ORDER)

    ordered: List[str] = []
    seen = set()
    for tool_name in enabled_tools:
        if tool_name in _DEFAULT_TOOL_GUIDANCE and tool_name not in seen:
            ordered.append(tool_name)
            seen.add(tool_name)
    return ordered


def _get_configured_enabled_tools() -> Optional[List[str]]:
    configured = parse_string_list(
        get_config_value("OPENAI_ENABLED_TOOLS", "ENABLED_TOOLS")
    )
    if configured is None:
        return None
    return _resolve_enabled_tools(configured)


def _list_tool_names(tool_defs: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for tool_def in tool_defs:
        function = tool_def.get("function", {})
        name = str(function.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def _print_available_tools() -> None:
    configured_enabled_tools = _get_configured_enabled_tools()
    enabled_set = set(
        configured_enabled_tools
        if configured_enabled_tools is not None
        else _DEFAULT_TOOL_GROUP_ORDER
    )

    print("可用工具组:")
    for group_name in _DEFAULT_TOOL_GROUP_ORDER:
        tool_names = _list_tool_names(AIAgent._get_tool_groups().get(group_name, []))
        enabled_label = "默认启用" if group_name in enabled_set else "默认关闭"
        print(f"  {group_name} [{enabled_label}]")
        guidance = _DEFAULT_TOOL_GUIDANCE.get(group_name)
        if guidance:
            print(f"    {guidance.lstrip('- ').strip()}")
        print(f"    tools: {', '.join(tool_names) if tool_names else '(none)'}")

    if configured_enabled_tools is None:
        print("\n当前未配置 OPENAI_ENABLED_TOOLS / ENABLED_TOOLS，默认启用全部工具组。")
    else:
        summary = ", ".join(configured_enabled_tools) if configured_enabled_tools else "(none)"
        print(f"\n当前配置默认启用工具组: {summary}")


def _format_tool_log_line(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "browser_use":
        action = str(arguments.get("action", "")).strip()
        if action:
            return f"TOOL: {tool_name} (action={action})"
    return f"TOOL: {tool_name}"


# 结束对话的关键词
_END_CONVERSATION_KEYWORDS = [
    "<<再见>>",
    "<<结束>>",
    "<<完成>>",
    "<<结束对话>>",
    "<<MESSAGE_END>>",
    "<<END>>",
]


def _should_end_conversation(response_content: Optional[str]) -> bool:
    """根据回复内容判断是否应该结束对话"""
    if not response_content:
        return False
    content_lower = response_content.lower()
    return any(keyword in content_lower for keyword in _END_CONVERSATION_KEYWORDS)


def build_default_system_prompt(enabled_tools: Optional[List[str]] = None) -> str:
    tool_groups = _resolve_enabled_tools(enabled_tools)
    tool_lines = [_DEFAULT_TOOL_GUIDANCE[name] for name in tool_groups]
    tool_summary = "、".join(tool_groups) if tool_groups else "无"

    sections = [
        "你是一个面向工程任务的 AI 助手，负责在多轮对话中准确理解需求、调用可用工具，并给出可执行、可验证的结果。",
        "工作原则：\n"
        "- 先理解目标，再决定是否需要工具；不要为了使用工具而使用工具。\n"
        "- 涉及项目文件、目录结构、代码实现、运行结果或环境状态时，优先通过工具获取事实，不要猜测。\n"
        "- 结论必须与实际工具结果一致；不要声称已经读取、修改、创建或执行了未实际完成的操作。\n"
        "- 当信息不足时，先继续收集上下文；确实缺少关键前提时，再明确指出缺口。\n"
        "- 回答保持直接、清晰、可落地，优先给出下一步结论或结果。",
        "编辑与执行要求：\n"
        "- 修改代码或文件前，先读取相关上下文，理解现有实现与影响范围。\n"
        "- 优先做最小必要变更，保留用户已有内容与风格，不主动重构无关部分。\n"
        "- 运行 Python 或 Bash 前，明确目的；优先用于验证、排查、测试、构建或获取事实。\n"
        "- 工具调用失败时，先根据报错调整参数、路径或方式，再决定是否需要向用户说明。\n"
        "- 若当前启用工具无法完成任务，应明确说明限制，并提供可行替代方案。",
        "当前可用工具组：" + tool_summary,
    ]

    if tool_lines:
        sections.append("工具使用说明：\n" + "\n".join(tool_lines))
    else:
        sections.append("当前未启用任何工具组，只能基于现有对话内容回答。")

    if "browser_use" in tool_groups:
        sections.append(
            "browser_use 使用规范：\n"
            "- 先检查页面会话是否存在；首次使用优先 action=start，再 action=open。\n"
            "- 在执行 click/type/hover/select_option 前，优先 action=snapshot 获取 refs。\n"
            "- 能用 ref 时优先 ref；仅当 ref 不可用时再使用 selector。\n"
            "- 涉及弹窗、上传、多标签页时，按 handle_dialog、file_upload、tabs 流程调用，不要跳步。\n"
            "- 导航后或关键动作后，可用 wait_for/snapshot/screenshot 验证页面状态，避免凭空断言。\n"
            "- 若工具返回错误，先依据报错修正参数重试（如 page_id、ref、selector、url、frame_selector），不要立即放弃。\n"
            "- 需要总结网页内容时，先通过 snapshot/evaluate 获取页面文本证据，再输出结论。\n"
            "- 任务结束时，若浏览器仍运行，调用 action=stop 释放资源。"
        )

    # 结束对话的提示词
    sections.append(
        "【结束指引】任务完成后，使用 <<再见>>、<<结束>>、<<完成>>、<<结束对话>>、<<MESSAGE_END>> 或 <<END>> 明确告知用户对话即将结束。"
    )

    return "\n\n".join(sections)


def _parse_prompt_params(values: List[str]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    key_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    for item in values:
        if "=" not in item:
            raise ValueError(f"无效参数 `{item}`，请使用 key=value 格式。")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key or not key_pattern.match(key):
            raise ValueError(
                f"无效参数名 `{key}`，参数名需匹配 [A-Za-z_][A-Za-z0-9_]*。"
            )
        params[key] = value

    return params


def _render_system_prompt(template: str, params: Dict[str, str]) -> str:
    if not params:
        return template
    # Only replace simple placeholders like {name}; keep JSON/style braces untouched.
    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return params.get(key, match.group(0))

    return pattern.sub(_replace, template)


class AIAgent:
    """
    一个支持多轮对话和 file_io 工具调用的 AI Agent。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        think: Optional[Any] = None,
        max_history_rounds: Optional[int] = None,
        max_tool_call_rounds: Optional[int] = None,
        enabled_tools: Optional[List[str]] = None,
        include_native_file_parts: Optional[bool] = None,
    ):
        load_environment()

        resolved_api_key = api_key or get_config_value("OPENAI_API_KEY")
        resolved_base_url = base_url or get_config_value("OPENAI_BASE_URL")
        resolved_model = model or get_config_value("OPENAI_MODEL") or "gpt-4.1-mini"
        resolved_think = think
        if resolved_think is None:
            resolved_think = parse_config_value(get_config_value("OPENAI_THINK", "THINK"))
        resolved_max_history_rounds = max_history_rounds
        if resolved_max_history_rounds is None:
            resolved_max_history_rounds = parse_positive_int(
                get_config_value("OPENAI_MAX_HISTORY_ROUNDS")
            )
        resolved_max_tool_call_rounds = max_tool_call_rounds
        if resolved_max_tool_call_rounds is None:
            resolved_max_tool_call_rounds = parse_positive_int(
                get_config_value("OPENAI_MAX_TOOL_CALL_ROUNDS")
            )
        if resolved_max_tool_call_rounds is None:
            resolved_max_tool_call_rounds = 8  # 默认值
        resolved_include_native_file_parts = include_native_file_parts
        if resolved_include_native_file_parts is None:
            resolved_include_native_file_parts = parse_bool(
                get_config_value(
                    "OPENAI_INCLUDE_NATIVE_FILE_PARTS",
                    "INCLUDE_NATIVE_FILE_PARTS",
                ),
                default=True,
            )

        if not resolved_api_key:
            raise ValueError("请在环境变量或 .env 文件中设置 OPENAI_API_KEY")

        client_kwargs = {"api_key": resolved_api_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        self.client = OpenAI(**client_kwargs)
        self.model = resolved_model
        self.think = resolved_think
        self.max_history_rounds = resolved_max_history_rounds
        self.max_tool_call_rounds = resolved_max_tool_call_rounds
        self.include_native_file_parts = resolved_include_native_file_parts
        self.last_think_content: Optional[str] = None
        self.conversation_history: List[Dict[str, Any]] = []
        self.uploaded_files: List[Dict[str, str]] = []
        resolved_enabled_tools = enabled_tools
        if resolved_enabled_tools is None:
            resolved_enabled_tools = _get_configured_enabled_tools()
        self.default_enabled_tools = resolved_enabled_tools
        self.enabled_tools = resolved_enabled_tools

    def upload_local_file(self, file_path: str, purpose: str = "user_data") -> Dict[str, str]:
        """上传本地文件到 OpenAI Files API，并记录到当前会话。"""
        expanded_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(expanded_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        last_error: Optional[Exception] = None
        for current_purpose in (purpose, "assistants"):
            try:
                with open(expanded_path, "rb") as fp:
                    uploaded = self.client.files.create(file=fp, purpose=current_purpose)
                file_info = {
                    "id": str(getattr(uploaded, "id", "")),
                    "filename": str(getattr(uploaded, "filename", os.path.basename(expanded_path))),
                    "purpose": current_purpose,
                    "path": expanded_path,
                }
                if not file_info["id"]:
                    raise ValueError("上传成功但未返回 file_id")
                self.uploaded_files.append(file_info)
                return file_info
            except Exception as error:
                last_error = error
                if current_purpose == "assistants":
                    break

        assert last_error is not None
        raise RuntimeError(f"文件上传失败: {last_error}")

    def get_uploaded_files(self) -> List[Dict[str, str]]:
        return list(self.uploaded_files)

    def clear_uploaded_files(self) -> None:
        self.uploaded_files = []

    def _trim_history_if_needed(self) -> None:
        if not self.max_history_rounds or self.max_history_rounds <= 0:
            return

        if not self.conversation_history:
            return

        head_messages: List[Dict[str, Any]] = []
        remaining_messages = self.conversation_history
        if self.conversation_history[0].get("role") == "system":
            head_messages = [self.conversation_history[0]]
            remaining_messages = self.conversation_history[1:]

        max_messages = self.max_history_rounds * 2
        if len(remaining_messages) <= max_messages:
            return

        trimmed_messages = remaining_messages[-max_messages:]
        self.conversation_history = head_messages + trimmed_messages

    _TOOL_GROUPS: Dict[str, List[Any]] = {}

    @classmethod
    def _get_tool_groups(cls) -> Dict[str, List[Any]]:
        if not cls._TOOL_GROUPS:
            cls._TOOL_GROUPS = {
                "file_io": FILE_IO_TOOLS,
                "dir_io": DIR_IO_TOOLS,
                "python_exec": PYTHON_EXEC_TOOLS,
                "bash_exec": BASH_EXEC_TOOLS,
                "browser_use": BROWSER_USE_TOOLS,
            }
        return cls._TOOL_GROUPS

    def _build_request_kwargs(self, stream: bool) -> Dict[str, Any]:
        if self.enabled_tools is None:
            tools = (
                FILE_IO_TOOLS
                + DIR_IO_TOOLS
                + PYTHON_EXEC_TOOLS
                + BASH_EXEC_TOOLS
                + BROWSER_USE_TOOLS
            )
        else:
            groups = self._get_tool_groups()
            tools = [t for g in self.enabled_tools for t in groups.get(g, [])]
        request_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": self.conversation_history,
            "tools": tools,
            "stream": stream,
        }
        if self.think is not None:
            request_kwargs["extra_body"] = {"think": self.think}
        return request_kwargs

    def _append_user_message(self, user_input: str) -> None:
        if self.uploaded_files:
            refs = [f"- {item['filename']}: {item['id']}" for item in self.uploaded_files]
            ref_text = (
                "可引用的 OpenAI 文件（已上传）:\n"
                + "\n".join(refs)
                + "\n如需引用，请在分析中使用对应 file_id。"
            )
            content: Any = [
                {"type": "text", "text": user_input},
                {"type": "text", "text": ref_text},
            ]
            if self.include_native_file_parts:
                for item in self.uploaded_files:
                    # 某些兼容端点不接受 file 类型消息片段；可通过开关关闭，仅保留 file_id 文本引用。
                    content.append({"type": "file", "file": {"file_id": item["id"]}})
            self.conversation_history.append({"role": "user", "content": content})
        else:
            self.conversation_history.append({"role": "user", "content": user_input})
        self.last_think_content = None
        self._trim_history_if_needed()

    def _execute_tool_and_append(self, tool_call: Dict[str, Any]) -> None:
        function = tool_call.get("function", {})
        tool_name = str(function.get("name", ""))
        raw_arguments = str(function.get("arguments", "{}"))
        arguments = parse_tool_arguments(raw_arguments)

        if "__error__" in arguments:
            tool_result = f"Error: {arguments['__error__']}"
        else:
            tool_result = dispatch_file_io_tool(tool_name, arguments)
            if tool_result.startswith("Error: Unknown tool"):
                tool_result = dispatch_dir_io_tool(tool_name, arguments)
            if tool_result.startswith("Error: Unknown tool"):
                tool_result = dispatch_python_exec_tool(tool_name, arguments)
            if tool_result.startswith("Error: Unknown tool"):
                tool_result = dispatch_bash_exec_tool(tool_name, arguments)
            if tool_result.startswith("Error: Unknown tool"):
                tool_result = dispatch_browser_use_tool(tool_name, arguments)

        tool_call_id = str(tool_call.get("id", ""))
        self.conversation_history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            }
        )

    @staticmethod
    def _serialize_tool_call(tool_call: Any) -> Dict[str, Any]:
        function = getattr(tool_call, "function", None)
        return {
            "id": getattr(tool_call, "id", ""),
            "type": getattr(tool_call, "type", "function"),
            "function": {
                "name": getattr(function, "name", ""),
                "arguments": getattr(function, "arguments", "{}"),
            },
        }

    def _run_with_tools_non_stream(self, user_input: str) -> Tuple[str, Optional[str]]:
        self._append_user_message(user_input)
        think_parts: List[str] = []

        for round_num in range(1, self.max_tool_call_rounds + 1):
            print(f"\n--- 对话轮数: {round_num}/{self.max_tool_call_rounds} ---")
            response = self.client.chat.completions.create(**self._build_request_kwargs(stream=False))
            message = response.choices[0].message

            think_content = extract_think_content(message)
            if think_content:
                think_parts.append(think_content)

            tool_calls = getattr(message, "tool_calls", None) or []
            assistant_reply = message.content or ""

            # 根据回复内容判断是否应该结束对话
            if _should_end_conversation(assistant_reply):
                print(f"\n✓ 对话结束: AI 主动结束（第 {round_num} 轮）")
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 无工具调用且无结束关键词，继续下一轮
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            serialized_tool_calls = [
                self._serialize_tool_call(tool_call) for tool_call in tool_calls
            ]
            self.conversation_history.append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                    "tool_calls": serialized_tool_calls,
                }
            )

            for serialized in serialized_tool_calls:
                self._execute_tool_and_append(serialized)

        fallback = "Error: Tool call rounds exceeded the maximum limit."
        self.conversation_history.append({"role": "assistant", "content": fallback})
        self.last_think_content = "\n".join(think_parts).strip() or None
        return fallback, self.last_think_content

    def _run_with_tools_stream(self, user_input: str) -> Tuple[str, Optional[str]]:
        self._append_user_message(user_input)
        think_parts: List[str] = []

        for round_num in range(1, self.max_tool_call_rounds + 1):
            print(f"\n--- 对话轮数: {round_num}/{self.max_tool_call_rounds} ---")
            stream = self.client.chat.completions.create(**self._build_request_kwargs(stream=True))
            assistant_reply, think_content, tool_calls = consume_stream_with_tool_calls(
                stream,
                emit_output=True,
            )

            if think_content:
                think_parts.append(think_content)

            # 根据回复内容判断是否应该结束对话
            if _should_end_conversation(assistant_reply):
                print(f"\n✓ 对话结束: AI 主动结束（第 {round_num} 轮）")
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 无工具调用且无结束关键词，继续下一轮
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            self.conversation_history.append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                    "tool_calls": tool_calls,
                }
            )

            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                tool_name = function.get("name", "unknown")
                raw_arguments = str(function.get("arguments", "{}"))
                arguments = parse_tool_arguments(raw_arguments)
                if "__error__" in arguments:
                    print(f"\nTOOL: {tool_name}")
                else:
                    print(f"\n{_format_tool_log_line(tool_name, arguments)}")
                self._execute_tool_and_append(tool_call)

            print()

        # 超过最大轮数限制
        print(f"\n✗ 对话结束: 超过最大轮数限制（{self.max_tool_call_rounds} 轮）")
        fallback = "Error: Tool call rounds exceeded the maximum limit."
        self.conversation_history.append({"role": "assistant", "content": fallback})
        self.last_think_content = "\n".join(think_parts).strip() or None
        return fallback, self.last_think_content

    def get_response(self, user_input: str) -> str:
        try:
            assistant_reply, _ = self._run_with_tools_non_stream(user_input)
            return assistant_reply
        except Exception as error:
            error_msg = f"发生错误: {error}"
            self.conversation_history.append({"role": "assistant", "content": error_msg})
            return error_msg

    def stream_response(self, user_input: str) -> str:
        try:
            assistant_reply, _ = self._run_with_tools_stream(user_input)
            if not assistant_reply:
                print("AI: ")
            return assistant_reply
        except Exception as error:
            error_msg = f"发生错误: {error}"
            self.conversation_history.append({"role": "assistant", "content": error_msg})
            return error_msg

    def start_conversation(self, system_prompt: Optional[str] = None) -> None:
        if system_prompt is None:
            system_prompt = build_default_system_prompt(self.enabled_tools)
        self.conversation_history = []
        self.conversation_history.append({"role": "system", "content": system_prompt})

    def reset_conversation(self) -> None:
        self.conversation_history = []

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        return self.conversation_history


def _apply_skills_to_agent(
    agent: "AIAgent",
    skill_names: List[str],
    cli_system_prompt: Optional[str],
    cli_prompt_params: Dict[str, str],
) -> SkillDefinition:
    """加载并应用 skill(s) 到 agent，重置会话，返回合并后的 SkillDefinition。"""
    loaded: List[SkillDefinition] = []
    for name in skill_names:
        sk = load_skill(name)
        loaded.append(sk)
    skill = merge_skills(loaded)

    if skill.model:
        agent.model = skill.model
    agent.enabled_tools = skill.tools if skill.tools is not None else agent.default_enabled_tools

    base = (
        cli_system_prompt
        or skill.system_prompt
        or get_config_value("OPENAI_SYSTEM_PROMPT", "SYSTEM_PROMPT")
        or build_default_system_prompt(agent.enabled_tools)
    )
    if skill.body:
        base = base + "\n\n" + skill.body
    effective_params = {**skill.params, **cli_prompt_params}
    agent.start_conversation(_render_system_prompt(base, effective_params))
    return skill


def main() -> int:
    load_environment()

    parser = argparse.ArgumentParser(description="AI Agent CLI")
    parser.add_argument(
        "input_message",
        nargs="*",
        help="可选：直接传入首条用户消息（例如: uv run agent 你好）",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="系统提示词模板，支持占位符，例如: '你是{role}，项目是{project}'",
    )
    parser.add_argument(
        "--prompt-param",
        action="append",
        default=[],
        help="提示词参数，格式 key=value，可重复传入。",
    )
    parser.add_argument(
        "--user-message",
        default=None,
        help="通过参数直接传入用户消息。",
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="NAME",
        help="加载指定 skill（可重复传入多个，例如: --skill coder --skill reviewer）。",
    )
    parser.add_argument(
        "--list-skills",
        action="store_true",
        help="列出所有可用 skill 并退出。",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="列出所有可用工具组及其工具并退出。",
    )
    parser.add_argument(
        "--all-skills",
        action="store_true",
        help="加载所有已发现的 skill。可与 --skill 组合使用。",
    )
    parser.add_argument(
        "--upload-file",
        action="append",
        default=[],
        metavar="PATH",
        help="上传本地文件到 OpenAI（可重复传入多个）。上传后会在对话中自动附带 file_id 引用信息。",
    )
    native_file_parts_group = parser.add_mutually_exclusive_group()
    native_file_parts_group.add_argument(
        "--native-file-parts",
        action="store_true",
        dest="native_file_parts",
        help="在用户消息中附带原生 file 片段（默认由环境变量控制，默认开启）。",
    )
    native_file_parts_group.add_argument(
        "--no-native-file-parts",
        action="store_false",
        dest="native_file_parts",
        help="不附带原生 file 片段，仅通过文本中的 file_id 引用文件。",
    )
    parser.set_defaults(native_file_parts=None)

    args = parser.parse_args()

    if args.list_tools:
        _print_available_tools()
        return 0

    # --list-skills: 列出 skill 后退出
    if args.list_skills:
        skills = list_skills()
        if not skills:
            print("未找到任何 skill。")
            active_dirs = "  ".join(str(p) for p in get_skill_search_dirs())
            print(f"搜索目录: {active_dirs}")
        else:
            print(f"找到 {len(skills)} 个 skill:")
            for sk in skills:
                line = f"  {sk.name}"
                if sk.description:
                    line += f": {sk.description}"
                if sk.model:
                    line += f"  [model={sk.model}]"
                if sk.tools is not None:
                    line += f"  [tools={','.join(sk.tools)}]"
                print(line)
        return 0

    # --skill/--all-skills: 加载并合并 skill
    skill: Optional[SkillDefinition] = None
    selected_skill_names: List[str] = list(args.skill)
    if args.all_skills:
        discovered = list_skills()
        if not discovered:
            print("错误: 未找到任何 skill，无法使用 --all-skills。")
            return 1
        discovered_names = [sk.name for sk in discovered]
        for name in discovered_names:
            if name not in selected_skill_names:
                selected_skill_names.append(name)

    if selected_skill_names:
        loaded: List[SkillDefinition] = []
        for skill_name in selected_skill_names:
            try:
                sk = load_skill(skill_name)
                loaded.append(sk)
                print(f"已加载 skill: {sk.name}")
                if sk.description:
                    print(f"  {sk.description}")
            except FileNotFoundError as e:
                print(f"错误: {e}")
                return 1
        skill = merge_skills(loaded)

    try:
        prompt_params = _parse_prompt_params(args.prompt_param)
    except ValueError as error:
        print(f"错误: {error}")
        return 1

    # 构建 agent，skill 可覆盖 model 与工具组
    agent_kwargs: Dict[str, Any] = {}
    if skill and skill.model:
        agent_kwargs["model"] = skill.model
    if skill and skill.tools is not None:
        agent_kwargs["enabled_tools"] = skill.tools
    if args.native_file_parts is not None:
        agent_kwargs["include_native_file_parts"] = args.native_file_parts

    try:
        agent = AIAgent(**agent_kwargs)
    except ValueError as error:
        print(f"错误: {error}")
        return 1

    original_model = agent.model
    original_enabled_tools = agent.default_enabled_tools
    cli_system_prompt = args.system_prompt

    # 系统提示词优先级: --system-prompt > skill.system_prompt > 环境变量 > 默认
    base_system_prompt = (
        args.system_prompt
        or (skill.system_prompt if skill else None)
        or get_config_value("OPENAI_SYSTEM_PROMPT", "SYSTEM_PROMPT")
        or build_default_system_prompt(agent.enabled_tools)
    )
    # skill 的 Markdown 正文追加到系统提示末尾
    if skill and skill.body:
        base_system_prompt = base_system_prompt + "\n\n" + skill.body

    # 参数优先级: --prompt-param 覆盖 skill 默认 params
    effective_params = {**(skill.params if skill else {}), **prompt_params}
    initial_system_prompt = _render_system_prompt(base_system_prompt, effective_params)

    agent.start_conversation(initial_system_prompt)

    print(f"AI Agent已启动，当前模型: {agent.model}")
    if agent.think is not None:
        print(f"当前 think 配置: {agent.think}")
    if agent.max_history_rounds:
        print(f"多轮上下文保留轮数: {agent.max_history_rounds}")
    if agent.enabled_tools is not None:
        print(f"已启用工具组: {agent.enabled_tools}")
    print(f"原生文件片段: {'开启' if agent.include_native_file_parts else '关闭'}")
    if effective_params:
        print(f"提示词参数: {effective_params}")

    if args.upload_file:
        for file_path in args.upload_file:
            try:
                file_info = agent.upload_local_file(file_path)
                print(
                    f"已上传文件: {file_info['filename']} -> {file_info['id']} "
                    f"(purpose={file_info['purpose']})"
                )
            except Exception as error:
                print(f"上传失败: {file_path} ({error})")
                return 1

    cli_user_message = args.user_message
    if not cli_user_message and args.input_message:
        cli_user_message = " ".join(args.input_message).strip()

    if cli_user_message:
        response = agent.stream_response(cli_user_message)
        if not response:
            print("AI: ")
        return 0

    print("命令: /reset 重置对话, /history 查看历史, /system <提示词> 更新系统提示")
    print("Skill: /skill list 列出, /skill load <名称> 加载, /skill unload 卸载, /skill reload 重载")
    print("File: /upload <本地路径> 上传文件, /files 查看已上传文件, /fileparts [on|off] 切换原生文件片段")
    print("输入 'quit'、'exit' 或 '退出' 结束对话。")

    while True:
        try:
            user_input = input("你: ").strip()
        except EOFError:
            print("\n输入结束，再见！")
            return 0
        except KeyboardInterrupt:
            print("\n对话被中断，再见！")
            return 0

        if not user_input:
            continue

        if user_input.lower() in ["quit", "exit", "退出"]:
            print("再见！")
            return 0

        if user_input == "/reset":
            agent.start_conversation(initial_system_prompt)
            print("会话已重置。\n")
            continue

        if user_input == "/files":
            uploaded_files = agent.get_uploaded_files()
            if not uploaded_files:
                print("当前没有已上传文件。\n")
                continue
            print("当前已上传文件:")
            for item in uploaded_files:
                print(
                    f"- {item.get('filename', '')} -> {item.get('id', '')} "
                    f"(purpose={item.get('purpose', '')})"
                )
            print()
            continue

        if user_input.startswith("/upload "):
            file_path = user_input[len("/upload ") :].strip()
            if not file_path:
                print("用法: /upload <本地文件路径>\n")
                continue
            try:
                file_info = agent.upload_local_file(file_path)
                print(
                    f"上传成功: {file_info['filename']} -> {file_info['id']} "
                    f"(purpose={file_info['purpose']})\n"
                )
            except Exception as error:
                print(f"上传失败: {error}\n")
            continue

        if user_input == "/fileparts":
            print(f"原生文件片段当前状态: {'开启' if agent.include_native_file_parts else '关闭'}\n")
            continue

        if user_input.startswith("/fileparts "):
            value = user_input[len("/fileparts ") :].strip().lower()
            if value in ("on", "true", "1"):
                agent.include_native_file_parts = True
                print("已开启原生文件片段。\n")
                continue
            if value in ("off", "false", "0"):
                agent.include_native_file_parts = False
                print("已关闭原生文件片段（仅保留 file_id 文本引用）。\n")
                continue
            print("用法: /fileparts [on|off]\n")
            continue

        if user_input == "/history":
            print("当前会话历史:")
            for index, message in enumerate(agent.get_conversation_history(), start=1):
                role = message.get("role", "unknown")
                content = message.get("content", "")
                print(f"{index}. [{role}] {content}")
            print()
            continue

        if user_input.startswith("/system "):
            system_prompt = user_input[len("/system ") :].strip()
            if not system_prompt:
                print("系统提示词不能为空。\n")
                continue
            agent.start_conversation(system_prompt)
            print("系统提示词已更新并重置会话。\n")
            continue

        if user_input in ("/skill list", "/skills"):
            skills = list_skills()
            if not skills:
                print("未找到任何 skill。")
                print(f"搜索目录: {'  '.join(str(p) for p in get_skill_search_dirs())}")
            else:
                print(f"找到 {len(skills)} 个 skill:")
                for sk in skills:
                    active = " [当前]" if sk.name in selected_skill_names else ""
                    line = f"  {sk.name}{active}"
                    if sk.description:
                        line += f": {sk.description}"
                    if sk.model:
                        line += f"  [model={sk.model}]"
                    if sk.tools is not None:
                        line += f"  [tools={','.join(sk.tools)}]"
                    print(line)
            print()
            continue

        if user_input.startswith("/skill load "):
            names = [n for n in user_input[len("/skill load "):].strip().split() if n]
            if not names:
                print("用法: /skill load <skill名称> [skill名称2 ...]\n")
                continue
            try:
                new_skill = _apply_skills_to_agent(agent, names, cli_system_prompt, prompt_params)
                selected_skill_names = names
                print(f"已加载 skill: {new_skill.name}")
                if new_skill.description:
                    print(f"  {new_skill.description}")
                if agent.enabled_tools is not None:
                    print(f"  工具组: {agent.enabled_tools}")
                print("会话已重置。\n")
            except FileNotFoundError as e:
                print(f"错误: {e}\n")
            continue

        if user_input == "/skill unload":
            if not selected_skill_names:
                print("当前没有加载任何 skill。\n")
                continue
            selected_skill_names = []
            agent.model = original_model
            agent.enabled_tools = original_enabled_tools
            base = (
                cli_system_prompt
                or get_config_value("OPENAI_SYSTEM_PROMPT", "SYSTEM_PROMPT")
                or build_default_system_prompt(agent.enabled_tools)
            )
            agent.start_conversation(_render_system_prompt(base, prompt_params))
            print("已卸载 skill，会话已重置。\n")
            continue

        if user_input == "/skill reload":
            if not selected_skill_names:
                print("当前没有加载任何 skill。\n")
                continue
            try:
                new_skill = _apply_skills_to_agent(agent, selected_skill_names, cli_system_prompt, prompt_params)
                print(f"已重载 skill: {new_skill.name}")
                print("会话已重置。\n")
            except FileNotFoundError as e:
                print(f"错误: {e}\n")
            continue

        try:
            response = agent.stream_response(user_input)
        except Exception as error:
            print(f"发生错误: {error}")
            return 1

        if not response:
            print("AI: ")
