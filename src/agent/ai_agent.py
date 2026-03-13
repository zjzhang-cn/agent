import argparse
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .bash_exec import BASH_EXEC_TOOLS, dispatch_bash_exec_tool
from .config import (
    get_config_value,
    load_environment,
    parse_config_value,
    parse_positive_int,
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

MAX_TOOL_CALL_ROUNDS = 8
DEFAULT_SYSTEM_PROMPT = (
    "你是一个乐于助人的AI助手，与用户进行多轮对话。"
    "当任务涉及文件、目录、Python 脚本/代码执行或 Bash 命令执行时，你可以使用相关工具。"
)


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
        enabled_tools: Optional[List[str]] = None,
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

        if not resolved_api_key:
            raise ValueError("请在环境变量或 .env 文件中设置 OPENAI_API_KEY")

        client_kwargs = {"api_key": resolved_api_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        self.client = OpenAI(**client_kwargs)
        self.model = resolved_model
        self.think = resolved_think
        self.max_history_rounds = resolved_max_history_rounds
        self.last_think_content: Optional[str] = None
        self.conversation_history: List[Dict[str, Any]] = []
        self.enabled_tools = enabled_tools  # None = 全部工具组

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
            }
        return cls._TOOL_GROUPS

    def _build_request_kwargs(self, stream: bool) -> Dict[str, Any]:
        if self.enabled_tools is None:
            tools = FILE_IO_TOOLS + DIR_IO_TOOLS + PYTHON_EXEC_TOOLS + BASH_EXEC_TOOLS
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

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            response = self.client.chat.completions.create(**self._build_request_kwargs(stream=False))
            message = response.choices[0].message

            think_content = extract_think_content(message)
            if think_content:
                think_parts.append(think_content)

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                assistant_reply = message.content or ""
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
                    "content": message.content or "",
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

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            stream = self.client.chat.completions.create(**self._build_request_kwargs(stream=True))
            assistant_reply, think_content, tool_calls = consume_stream_with_tool_calls(
                stream,
                emit_output=True,
            )

            if think_content:
                think_parts.append(think_content)

            if not tool_calls:
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
                print(f"\nTOOL: {tool_name}")
                self._execute_tool_and_append(tool_call)

            print()

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

    def start_conversation(self, system_prompt: str = "你是一个有用的AI助手。") -> None:
        self.conversation_history = []
        self.conversation_history.append({"role": "system", "content": system_prompt})

    def reset_conversation(self) -> None:
        self.conversation_history = []

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        return self.conversation_history


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
        "--all-skills",
        action="store_true",
        help="加载所有已发现的 skill。可与 --skill 组合使用。",
    )

    args = parser.parse_args()

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

    try:
        agent = AIAgent(**agent_kwargs)
    except ValueError as error:
        print(f"错误: {error}")
        return 1

    # 系统提示词优先级: --system-prompt > skill.system_prompt > 环境变量 > 默认
    base_system_prompt = (
        args.system_prompt
        or (skill.system_prompt if skill else None)
        or get_config_value("OPENAI_SYSTEM_PROMPT", "SYSTEM_PROMPT")
        or DEFAULT_SYSTEM_PROMPT
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
    if effective_params:
        print(f"提示词参数: {effective_params}")

    cli_user_message = args.user_message
    if not cli_user_message and args.input_message:
        cli_user_message = " ".join(args.input_message).strip()

    if cli_user_message:
        response = agent.stream_response(cli_user_message)
        if not response:
            print("AI: ")
        return 0

    print("命令: /reset 重置对话, /history 查看历史, /system <提示词> 更新系统提示")
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

        try:
            response = agent.stream_response(user_input)
        except Exception as error:
            print(f"发生错误: {error}")
            return 1

        if not response:
            print("AI: ")
