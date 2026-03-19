"""
提示词工具模块。
提供系统提示词构建功能。
"""
import os
import platform
from typing import List, Optional

from .tool_utils import DEFAULT_TOOL_GUIDANCE, resolve_enabled_tools


def build_default_system_prompt(enabled_tools: Optional[List[str]] = None) -> str:
    """构建默认系统提示词"""
    tool_groups = resolve_enabled_tools(enabled_tools)
    tool_lines = [DEFAULT_TOOL_GUIDANCE[name] for name in tool_groups]
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