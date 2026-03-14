import argparse
import re
from typing import Any, Dict, List, Optional

from .ai_agent import (
    _DEFAULT_TOOL_GROUP_ORDER,
    _DEFAULT_TOOL_GUIDANCE,
    _get_configured_enabled_tools,
    AIAgent,
    build_default_system_prompt,
)
from .config import get_config_value, load_environment
from .skill import SkillDefinition, get_skill_search_dirs, list_skills, load_skill, merge_skills


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


def _is_env_model_configured() -> bool:
    configured_model = get_config_value("OPENAI_MODEL")
    if configured_model is None:
        return False
    return bool(str(configured_model).strip())


def _apply_skills_to_agent(
    agent: AIAgent,
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

    if skill.model and not _is_env_model_configured():
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
            except FileNotFoundError as error:
                print(f"错误: {error}")
                return 1
        skill = merge_skills(loaded)

    try:
        prompt_params = _parse_prompt_params(args.prompt_param)
    except ValueError as error:
        print(f"错误: {error}")
        return 1

    agent_kwargs: Dict[str, Any] = {}
    if skill and skill.model and not _is_env_model_configured():
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

    base_system_prompt = (
        args.system_prompt
        or (skill.system_prompt if skill else None)
        or get_config_value("OPENAI_SYSTEM_PROMPT", "SYSTEM_PROMPT")
        or build_default_system_prompt(agent.enabled_tools)
    )
    if skill and skill.body:
        base_system_prompt = base_system_prompt + "\n\n" + skill.body

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
            except FileNotFoundError as error:
                print(f"错误: {error}\n")
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
                new_skill = _apply_skills_to_agent(
                    agent,
                    selected_skill_names,
                    cli_system_prompt,
                    prompt_params,
                )
                print(f"已重载 skill: {new_skill.name}")
                print("会话已重置。\n")
            except FileNotFoundError as error:
                print(f"错误: {error}\n")
            continue

        try:
            response = agent.stream_response(user_input)
        except Exception as error:
            print(f"发生错误: {error}")
            return 1

        if not response:
            print("AI: ")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())