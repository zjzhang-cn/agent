import argparse
import json
import os
import shlex
from typing import Any, Dict, Optional

from .browser_use import browser_use_tool


def _load_args_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"--args-json 不是合法 JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("--args-json 必须是 JSON 对象，例如: '{\"page_id\":\"demo\"}'")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "browser_use 测试 CLI。支持通过 --action + 参数直接调用 "
            "agent.browser_use.browser_use_tool。"
        )
    )
    parser.add_argument("--action", help="动作名，例如: start/open/click/evaluate/stop")
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="进入多次交互模式（REPL），可连续执行多条 browser_use 指令",
    )
    parser.add_argument("--args-json", default="", help="额外参数 JSON 对象，作为基础参数")

    parser.add_argument("--url", default=None)
    parser.add_argument("--page-id", default=None)
    parser.add_argument("--selector", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--code", default=None)
    parser.add_argument("--path", default=None)
    parser.add_argument("--wait", type=int, default=None)
    parser.add_argument("--full-page", action="store_true", help="等价 full_page=true")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--level", default=None)
    parser.add_argument("--filename", default=None)

    parser.add_argument("--accept", dest="accept", action="store_true")
    parser.add_argument("--reject", dest="accept", action="store_false")
    parser.set_defaults(accept=None)

    parser.add_argument("--prompt-text", default=None)
    parser.add_argument("--ref", default=None)
    parser.add_argument("--paths-json", default=None)
    parser.add_argument("--fields-json", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--slowly", action="store_true")
    parser.add_argument("--include-static", action="store_true")
    parser.add_argument("--screenshot-type", default=None)
    parser.add_argument("--snapshot-filename", default=None)
    parser.add_argument("--double-click", action="store_true")
    parser.add_argument("--button", default=None)
    parser.add_argument("--modifiers-json", default=None)
    parser.add_argument("--start-ref", default=None)
    parser.add_argument("--end-ref", default=None)
    parser.add_argument("--start-selector", default=None)
    parser.add_argument("--end-selector", default=None)
    parser.add_argument("--values-json", default=None)
    parser.add_argument("--tab-action", default=None)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--wait-time", type=float, default=None)
    parser.add_argument("--text-gone", default=None)
    parser.add_argument("--frame-selector", default=None)

    parser.add_argument("--headed", dest="headed", action="store_true")
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.set_defaults(headed=None)

    parser.add_argument("--force-stop", action="store_true")
    parser.add_argument("--compact", action="store_true", help="输出紧凑 JSON（默认美化输出）")
    return parser


def _merge_cli_args(base: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    payload = dict(base)
    if args.action is not None:
        payload["action"] = args.action

    optional_fields: Dict[str, Optional[Any]] = {
        "url": args.url,
        "page_id": args.page_id,
        "selector": args.selector,
        "text": args.text,
        "code": args.code,
        "path": args.path,
        "wait": args.wait,
        "width": args.width,
        "height": args.height,
        "level": args.level,
        "filename": args.filename,
        "accept": args.accept,
        "prompt_text": args.prompt_text,
        "ref": args.ref,
        "paths_json": args.paths_json,
        "fields_json": args.fields_json,
        "key": args.key,
        "screenshot_type": args.screenshot_type,
        "snapshot_filename": args.snapshot_filename,
        "button": args.button,
        "modifiers_json": args.modifiers_json,
        "start_ref": args.start_ref,
        "end_ref": args.end_ref,
        "start_selector": args.start_selector,
        "end_selector": args.end_selector,
        "values_json": args.values_json,
        "tab_action": args.tab_action,
        "index": args.index,
        "wait_time": args.wait_time,
        "text_gone": args.text_gone,
        "frame_selector": args.frame_selector,
        "headed": args.headed,
    }
    for key, value in optional_fields.items():
        if value is not None:
            payload[key] = value

    # 这几个布尔开关是单向开关，只在显式传入时覆盖。
    if args.full_page:
        payload["full_page"] = True
    if args.submit:
        payload["submit"] = True
    if args.slowly:
        payload["slowly"] = True
    if args.include_static:
        payload["include_static"] = True
    if args.double_click:
        payload["double_click"] = True
    if args.force_stop:
        payload["force_stop"] = True

    return payload


def _parse_kv_value(raw: str) -> Any:
    text = (raw or "").strip()
    if text == "":
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _parse_interactive_line(line: str) -> Dict[str, Any]:
    text = line.strip()
    if not text:
        raise ValueError("空命令")

    if text.startswith("{"):
        payload = _load_args_json(text)
        if not str(payload.get("action", "")).strip():
            raise ValueError("JSON 命令必须包含 action 字段")
        return payload

    tokens = shlex.split(text)
    if not tokens:
        raise ValueError("空命令")

    payload: Dict[str, Any] = {"action": tokens[0]}
    for token in tokens[1:]:
        if "=" not in token:
            raise ValueError(f"无效参数 `{token}`，请使用 key=value")
        key, value = token.split("=", 1)
        key = key.strip().replace("-", "_")
        if not key:
            raise ValueError(f"无效参数 `{token}`")
        payload[key] = _parse_kv_value(value)
    return payload


def _print_result(raw: str, compact: bool) -> bool:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return False

    if compact:
        print(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return bool(parsed.get("ok"))


def _run_interactive(compact: bool) -> int:
    print("已进入 browser-use-test 交互模式。")
    print("输入格式1: action key=value key2=value2")
    print('输入格式2: {"action":"open","url":"https://example.com"}')
    print("输入 /help 查看帮助，输入 quit/exit/退出 结束。")

    while True:
        try:
            line = input("browser-use> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\n已中断。")
            return 130

        if not line:
            continue

        lower = line.lower()
        if lower in ("quit", "exit", "退出"):
            return 0
        if line == "/help":
            print("示例:")
            print("  start headed=true")
            print("  open page_id=demo url=https://example.com")
            print("  click page_id=demo selector=\"text=More information\"")
            print("  evaluate page_id=demo code='() => location.href'")
            print("  stop force_stop=true")
            continue

        try:
            payload = _parse_interactive_line(line)
        except ValueError as error:
            print(f"命令错误: {error}")
            continue

        raw = browser_use_tool(**payload)
        _print_result(raw, compact)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # CLI 测试工具默认使用可见浏览器并尝试前置窗口，便于人工观察。
    os.environ.setdefault("COPAW_BROWSER_HEADED", "1")
    os.environ.setdefault("COPAW_BROWSER_BRING_TO_FRONT", "1")

    try:
        base = _load_args_json(args.args_json)
    except ValueError as error:
        print(str(error))
        return 2

    if args.interactive:
        return _run_interactive(args.compact)

    if not args.action:
        parser.error("必须提供 --action，或使用 --interactive 进入交互模式")
        return 2

    payload = _merge_cli_args(base, args)
    raw = browser_use_tool(**payload)
    ok = _print_result(raw, args.compact)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())