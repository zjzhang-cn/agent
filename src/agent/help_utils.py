"""
帮助工具模块。
提供帮助命令检测和帮助内容生成功能。
"""
from typing import List, Optional

from .tool_utils import DEFAULT_TOOL_GUIDANCE, resolve_enabled_tools


# 系统指令帮助命令
HELP_COMMANDS = ["help", "帮助", "?", "？", "指令", "命令", "help me", "help!"]


def is_help_command(user_input: str) -> bool:
    """检测用户输入是否为帮助命令"""
    if not user_input:
        return False
    content_lower = user_input.lower().strip()
    return any(cmd.lower() in content_lower for cmd in HELP_COMMANDS)


def build_help_content(enabled_tools: Optional[List[str]] = None) -> str:
    """构建帮助内容"""
    tool_groups = resolve_enabled_tools(enabled_tools)
    tool_lines = [DEFAULT_TOOL_GUIDANCE[name] for name in tool_groups]
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