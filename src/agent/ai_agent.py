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
from .error_handling import (
    format_exception_details,
    log_conversation,
    log_tool_call,
    log_exception,
    AgentError,
    ToolExecutionError,
    ConfigurationError,
)
from .http_client import build_openai_http_client
from .help_utils import HELP_COMMANDS, is_help_command, build_help_content
from .tool_utils import (
    DEFAULT_TOOL_GROUP_ORDER,
    DEFAULT_TOOL_GUIDANCE,
    resolve_enabled_tools,
    get_configured_enabled_tools,
    format_tool_log_line,
)
from .conversation_utils import END_CONVERSATION_KEYWORDS, should_end_conversation
from .prompt_utils import build_default_system_prompt




















class AIAgent:
    """
    一个支持多轮对话和 file_io 工具调用的 AI Agent。
    """

    # 工具分发器映射
    _TOOL_DISPATCHERS = {
        "file_io": dispatch_file_io_tool,
        "dir_io": dispatch_dir_io_tool,
        "python_exec": dispatch_python_exec_tool,
        "bash_exec": dispatch_bash_exec_tool,
        "browser_use": dispatch_browser_use_tool,
    }

    # 工具名到工具组名的映射
    _TOOL_NAME_TO_GROUP = {
        # file_io 工具
        "read_file": "file_io",
        "write_file": "file_io",
        "edit_file": "file_io",
        "append_file": "file_io",
        # dir_io 工具
        "list_directory": "dir_io",
        "create_directory": "dir_io",
        "remove_directory": "dir_io",
        "move_directory": "dir_io",
        "copy_directory": "dir_io",
        "directory_exists": "dir_io",
        # python_exec 工具
        "run_python_script": "python_exec",
        "run_python_code": "python_exec",
        # bash_exec 工具
        "run_bash_command": "bash_exec",
        "run_shell_command": "bash_exec",
        # browser_use 工具
        "browser_use": "browser_use",
    }

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
            or ".history.log"
        )

        if not resolved_api_key:
            raise ValueError("请在环境变量或 .env 文件中设置 OPENAI_API_KEY")

        client_kwargs: Dict[str, Any] = {"api_key": resolved_api_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        http_client = build_openai_http_client()
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
            resolved_enabled_tools = get_configured_enabled_tools()
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
                last_error_text = format_exception_details(error)
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
        if not is_help_command(user_input):
            return None
        help_content = build_help_content(self.enabled_tools)
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
            # 使用工具名到工具组名映射获取分发函数
            group_name = self._TOOL_NAME_TO_GROUP.get(tool_name)
            if group_name:
                dispatcher = self._TOOL_DISPATCHERS.get(group_name)
                if dispatcher:
                    tool_result = dispatcher(tool_name, arguments)
                else:
                    # 如果找不到分发函数，回退到链式检查
                    tool_result = self._fallback_tool_dispatch(tool_name, arguments)
            else:
                # 工具名不在映射中，回退到链式检查（兼容未来可能添加的工具）
                tool_result = self._fallback_tool_dispatch(tool_name, arguments)

        # 记录工具调用日志
        success = not tool_result.startswith("Error:")
        log_tool_call(
            tool_name=tool_name,
            arguments=arguments,
            success=success,
            result=tool_result if success else None,
            error=None if success else Exception(tool_result)
        )

        tool_call_id = str(tool_call.get("id", ""))
        self.conversation_history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            }
        )

    def _fallback_tool_dispatch(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """回退工具分发：链式检查各个工具组（兼容未来可能添加的工具）"""
        tool_result = dispatch_file_io_tool(tool_name, arguments)
        if tool_result.startswith("Error: Unknown tool"):
            tool_result = dispatch_dir_io_tool(tool_name, arguments)
        if tool_result.startswith("Error: Unknown tool"):
            tool_result = dispatch_python_exec_tool(tool_name, arguments)
        if tool_result.startswith("Error: Unknown tool"):
            tool_result = dispatch_bash_exec_tool(tool_name, arguments)
        if tool_result.startswith("Error: Unknown tool"):
            tool_result = dispatch_browser_use_tool(tool_name, arguments)
        return tool_result

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
            if should_end_conversation(assistant_reply):
                print(f"\n✓ 对话结束: AI 主动结束（第 {round_num} 轮）")
                
                # 记录对话到日志
                log_conversation(user_input, assistant_reply, self.log_file_path)
                
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 记录对话到日志
                log_conversation(user_input, assistant_reply, self.log_file_path)
                
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
        log_conversation(user_input, fallback, self.log_file_path)
        
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
            if should_end_conversation(assistant_reply):
                print(f"\n✓ 对话结束: AI 主动结束（第 {round_num} 轮）")
                
                # 记录对话到日志
                log_conversation(user_input, assistant_reply, self.log_file_path)
                
                self.conversation_history.append(
                    {"role": "assistant", "content": assistant_reply}
                )
                self.last_think_content = "\n".join(think_parts).strip() or None
                return assistant_reply, self.last_think_content

            if not tool_calls:
                # 记录对话到日志
                log_conversation(user_input, assistant_reply, self.log_file_path)
                
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
                    print(f"\n{format_tool_log_line(tool_name, arguments)}")
                self._execute_tool_and_append(tool_call)

            print()

        # 超过最大轮数限制
        print(f"\n✗ 对话结束: 超过最大轮数限制（{self.max_tool_call_rounds} 轮）")
        
        # 记录对话到日志
        log_conversation(user_input, "Error: Tool call rounds exceeded the maximum limit.", self.log_file_path)
        
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
            error_msg = format_exception_details(error)
            print(error_msg)
            
            # 记录错误到日志
            log_conversation(user_input, error_msg, self.log_file_path)
            
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
            error_msg = format_exception_details(error)
            print(error_msg)
            
            # 记录错误到日志
            log_conversation(user_input, error_msg, self.log_file_path)
            
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