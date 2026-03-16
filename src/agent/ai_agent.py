import os
import traceback
from typing import Any, Dict, List, Optional, Tuple
import platform
import datetime

import httpx
from openai import OpenAI

from .bash_exec import (
    BASH_EXEC_TOOLS,
    dispatch_bash_exec_tool,
    get_effective_shell_name,
)
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
from .streaming import (
    consume_stream_with_tool_calls,
    extract_think_content,
    parse_tool_arguments,
)

# 系统指令帮助命令
_HELP_COMMANDS = ["help", "帮助", "?", "？", "指令", "命令", "help me", "help!"]

def _log_conversation(user_input: str, assistant_reply: str, log_file_path: str = "conversation.log"):
    """记录对话到日志文件"""
    if not log_file_path:
        log_file_path = "conversation.log"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] 用户: {user_input}\n")
            log_file.write(f"[{timestamp}] AI: {assistant_reply}\n")
            log_file.write("-" * 50 + "\n")
    except Exception as error:
        print(f"日志写入失败: {error}")


def _format_exception_details(error: Exception) -> str:
    """格式化异常详情，便于定位 OpenAI 调用问题。"""
    error_module = type(error).__module__
    if error_module.startswith("openai"):
        title = "调用 OpenAI 接口时发生异常"
    else:
        title = "发生异常"

    lines = [
        f"{title}:",
        f"异常类型: {type(error).__name__}",
        f"异常信息: {error}",
    ]

    for attr, label in (
        ("status_code", "HTTP 状态码"),
        ("request_id", "请求 ID"),
        ("code", "错误码"),
        ("param", "错误参数"),
        ("type", "错误类型"),
    ):
        value = getattr(error, attr, None)
        if value not in (None, ""):
            lines.append(f"{label}: {value}")

    body = getattr(error, "body", None)
    if body not in (None, ""):
        lines.append(f"响应体: {body}")

    response = getattr(error, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if response_status not in (None, ""):
            lines.append(f"响应状态码: {response_status}")

    if error.__cause__:
        lines.append(f"原始原因: {type(error.__cause__).__name__}: {error.__cause__}")

    traceback_text = traceback.format_exc().strip()
    if traceback_text and traceback_text != "NoneType: None":
        lines.append("Traceback:")
        lines.append(traceback_text)

    return "\n".join(lines)


def _build_openai_http_client() -> Optional[httpx.Client]:
    """根据环境变量构建 OpenAI 的 HTTP 客户端（支持自签名证书信任）。"""
    verify_ssl = parse_bool(
        get_config_value("OPENAI_SSL_VERIFY", "OPENAI_TLS_VERIFY"),
        default=True,
    )
    cert_path = get_config_value("OPENAI_CA_BUNDLE", "SSL_CERT_FILE")

    if cert_path:
        cert_path = os.path.abspath(os.path.expanduser(cert_path))
        if not os.path.isfile(cert_path):
            raise ValueError(f"OPENAI_CA_BUNDLE 指定的证书文件不存在: {cert_path}")

    if not verify_ssl:
        print("警告: 已禁用 OpenAI TLS 证书校验（OPENAI_SSL_VERIFY=false），仅建议在内网调试场景使用。")
        return httpx.Client(verify=False)

    if cert_path:
        print(f"已加载 OpenAI CA 证书: {cert_path}")
        return httpx.Client(verify=cert_path)

    return None

def _is_help_command(user_input: str) -> bool:
    """检测用户输入是否为帮助命令"""
    if not user_input:
        return False
    content_lower = user_input.lower().strip()
    return any(cmd.lower() in content_lower for cmd in _HELP_COMMANDS)


def _build_help_content(enabled_tools: Optional[List[str]] = None) -> str:
    """构建帮助内容"""
    tool_groups = _resolve_enabled_tools(enabled_tools)
    tool_lines = [_DEFAULT_TOOL_GUIDANCE[name] for name in tool_groups]
    tool_summary = "、".join(tool_groups) if tool_groups else "无"

    help_text = """📖 **可用指令帮助**

**对话控制指令：**
- `结束` / `exit` / `quit` - 结束当前对话
- `帮助` / `help` / `?` - 显示本帮助信息

**CLI 命令（输入以 / 开头）：**
- `/reset` - 重置当前会话（清空历史并重新应用系统提示）
- `/history` - 查看当前会话历史
- `/system <提示词>` - 更新系统提示词
- `/files` - 查看当前已上传文件列表
- `/upload <本地路径>` - 上传本地文件到会话
- `/fileparts` - 查看原生文件片段开关状态
- `/fileparts on|off` - 开启/关闭原生文件片段

**Skill 命令：**
- `/skill list` - 列出可用技能
- `/skill load <名称>` - 加载指定技能
- `/skill unload` - 卸载当前技能
- `/skill reload` - 重新加载当前技能

**当前可用工具组：** """ + tool_summary + """

**工具使用说明：**
"""
    help_text += "\n".join(tool_lines)
    help_text += """

**使用提示：**
- 直接输入你的需求，我会根据需要调用工具来帮助你
- 需要查看命令清单时，输入 `help`、`帮助` 或 `/help`
- 如果需要结束对话，说"结束"或"exit"即可
- 想管理会话或技能时，优先使用 `/reset`、`/history`、`/skill ...`
"""
    return help_text


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
    "python_exec": "- Python 工具：可以执行 Python 脚本或代码片段。适合做逻辑验证、生成结果、复现问题；只有在确实需要时才执行。",
    "bash_exec": "- Shell 工具：可以执行跨平台 Shell 命令。Windows 默认使用 PowerShell，类 Unix 默认使用 Bash；也可通过 shell 参数指定 auto/bash/powershell/cmd。",
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


def _format_tool_log_line(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "browser_use":
        action = str(arguments.get("action", "")).strip()
        if not action:
            return f"TOOL: {tool_name}"

        if action == "click":
            details: List[str] = [f"action={action}"]
            for key in ("page_id", "ref", "selector", "frame_selector", "timeout_ms"):
                value = arguments.get(key)
                if value not in (None, ""):
                    details.append(f"{key}={value}")
            return f"TOOL: {tool_name} (" + ", ".join(details) + ")"

        return f"TOOL: {tool_name} (action={action})"

    if tool_name in ("run_bash_command", "run_shell_command"):
        command = arguments.get("command", "")
        effective_shell = get_effective_shell_name(arguments.get("shell"))
        return f"TOOL: {tool_name} (shell={effective_shell}, command={command})"

    if tool_name == "run_python_script":
        script_path = arguments.get("script_path", "")
        return f"TOOL: {tool_name} (script={script_path})"

    if tool_name == "run_python_code":
        code = arguments.get("code", "")
        # 只显示第一行或截断
        code_preview = code.split("\n")[0][:50] if code else ""
        return f"TOOL: {tool_name} (code={code_preview})"

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

    # 添加主机环境信息
    system_info = {
        "os_type": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine_arch": platform.machine(),
        "processor": platform.processor(),
        "platform_details": platform.platform(),
    }
    
    # 构建环境描述
    env_description = f"主机环境信息：\n"
    env_description += f"- 操作系统类型: {system_info['os_type']}\n"
    env_description += f"- 操作系统版本: {system_info['os_version']}\n"
    env_description += f"- 操作系统发行版: {system_info['platform_details']}\n"
    env_description += f"- 系统架构: {system_info['machine_arch']}\n"
    if system_info['processor']:
        env_description += f"- 处理器: {system_info['processor']}\n"
    
    # 检测命令解释器类型
    shell_type = os.environ.get('SHELL', 'Unknown')
    if platform.system() == "Windows":
        # Windows 系统可能使用 PowerShell 或 CMD
        if os.environ.get('PSModulePath'):
            shell_info = "命令解释器类型: PowerShell"
        else:
            shell_info = f"命令解释器类型: {os.environ.get('COMSPEC', 'CMD')}"
    else:
        # Unix-like 系统使用 SHELL 环境变量
        shell_info = f"命令解释器类型: {shell_type}"
    
    env_description += f"- {shell_info}\n"

    sections = [
        "你是一个面向工程任务的 AI 助手，负责在多轮对话中准确理解需求、调用可用工具，并给出可执行、可验证的结果。",
        env_description,
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
        log_file_path: Optional[str] = None,  # 新增参数
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
            
        # 解析日志文件路径配置
        self.log_file_path = (
            log_file_path
            or get_config_value("LOG_FILE_PATH")
            or "conversation.log"
        )

        if not resolved_api_key:
            raise ValueError("请在环境变量或 .env 文件中设置 OPENAI_API_KEY")

        client_kwargs: Dict[str, Any] = {"api_key": resolved_api_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        http_client = _build_openai_http_client()
        if http_client is not None:
            client_kwargs["http_client"] = http_client

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
        last_error_text: Optional[str] = None
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
                last_error_text = _format_exception_details(error)
                if current_purpose == "assistants":
                    break

        assert last_error is not None
        if last_error_text:
            raise RuntimeError(f"文件上传失败:\n{last_error_text}") from last_error
        raise RuntimeError(f"文件上传失败: {last_error}") from last_error

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

    def _try_handle_help_command(self, user_input: str) -> Optional[str]:
        if not _is_help_command(user_input):
            return None
        help_content = _build_help_content(self.enabled_tools)
        self.conversation_history.append({"role": "user", "content": user_input})
        self.conversation_history.append({"role": "assistant", "content": help_content})
        self.last_think_content = None
        self._trim_history_if_needed()
        return help_content

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
                
                # 记录对话到日志
                _log_conversation(user_input, assistant_reply, self.log_file_path)
                
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 记录对话到日志
                _log_conversation(user_input, assistant_reply, self.log_file_path)
                
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
        
        # 记录对话到日志
        _log_conversation(user_input, fallback, self.log_file_path)
        
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
                
                # 记录对话到日志
                _log_conversation(user_input, assistant_reply, self.log_file_path)
                
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 记录对话到日志
                _log_conversation(user_input, assistant_reply, self.log_file_path)
                
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
        
        # 记录对话到日志
        _log_conversation(user_input, "Error: Tool call rounds exceeded the maximum limit.", self.log_file_path)
        
        fallback = "Error: Tool call rounds exceeded the maximum limit."
        self.conversation_history.append({"role": "assistant", "content": fallback})
        self.last_think_content = "\n".join(think_parts).strip() or None
        return fallback, self.last_think_content

    def get_response(self, user_input: str) -> str:
        help_content = self._try_handle_help_command(user_input)
        if help_content is not None:
            return help_content
        try:
            assistant_reply, _ = self._run_with_tools_non_stream(user_input)
            return assistant_reply
        except Exception as error:
            error_msg = _format_exception_details(error)
            print(error_msg)
            
            # 记录错误到日志
            _log_conversation(user_input, error_msg, self.log_file_path)
            
            self.conversation_history.append({"role": "assistant", "content": error_msg})
            return error_msg

    def stream_response(self, user_input: str) -> str:
        help_content = self._try_handle_help_command(user_input)
        if help_content is not None:
            print(help_content)
            return help_content
        try:
            assistant_reply, _ = self._run_with_tools_stream(user_input)
            if not assistant_reply:
                print("AI: ")
            return assistant_reply
        except Exception as error:
            error_msg = _format_exception_details(error)
            print(error_msg)
            
            # 记录错误到日志
            _log_conversation(user_input, error_msg, self.log_file_path)
            
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