"""
工具工具模块。
提供工具组配置、工具名称映射和工具日志格式化功能。
"""
from typing import Any, Dict, List, Optional

from .bash_exec import get_effective_shell_name
from .config import get_config_value, parse_string_list


# 默认工具组顺序
DEFAULT_TOOL_GROUP_ORDER = [
    "file_io",
    "dir_io",
    "python_exec",
    "bash_exec",
    "browser_use",
]

# 默认工具组使用指导
DEFAULT_TOOL_GUIDANCE = {
    "file_io": "- 文件工具：可以读取、写入、编辑、追加文本文件。修改前优先读取相关文件，变更应保持最小且避免误改无关内容。",
    "dir_io": "- 目录工具：可以列出、创建、删除、移动、复制目录，以及检查目录是否存在。执行前先确认路径和影响范围。",
    "python_exec": "- Python 工具：可以执行 Python 脚本或代码片段。适合做逻辑验证、生成结果、复现问题；只有在确实需要时才执行。",
    "bash_exec": "- Shell 工具：可以执行跨平台 Shell 命令。Windows 默认使用 PowerShell，类 Unix 默认使用 Bash；也可通过 shell 参数指定 auto/bash/powershell/cmd。",
    "browser_use": (
        "- 浏览器工具：通过 Playwright 执行网页自动化（页面打开、交互、截图、快照、网络与控制台观察）。"
        "推荐流程：start -> open/navigate -> snapshot -> 使用 ref 执行 click/type/hover/select_option -> 必要时 screenshot/pdf -> stop。"
    ),
}


def resolve_enabled_tools(enabled_tools: Optional[List[str]]) -> List[str]:
    """解析启用的工具组列表，确保顺序符合默认顺序且去重。"""
    if enabled_tools is None:
        return list(DEFAULT_TOOL_GROUP_ORDER)

    ordered: List[str] = []
    seen = set()
    for tool_name in enabled_tools:
        if tool_name in DEFAULT_TOOL_GUIDANCE and tool_name not in seen:
            ordered.append(tool_name)
            seen.add(tool_name)
    return ordered


def get_configured_enabled_tools() -> Optional[List[str]]:
    """从环境变量获取配置的启用工具组列表。"""
    configured = parse_string_list(
        get_config_value("OPENAI_ENABLED_TOOLS", "ENABLED_TOOLS")
    )
    if configured is None:
        return None
    return resolve_enabled_tools(configured)


def format_tool_log_line(tool_name: str, arguments: Dict[str, Any]) -> str:
    """格式化工具调用日志行，提供友好的输出信息。"""
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