import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .config import (
    get_browser_auto_stop_enabled,
    get_browser_bring_to_front_enabled,
    get_browser_headless_default,
    get_playwright_chromium_executable_path,
    get_browser_use_sys_default,
    get_system_default_browser,
    is_running_in_container,
    get_browser_launch_args,
)

logger = logging.getLogger(__name__)

WORKING_DIR = Path.cwd()
_BROWSER_IDLE_TIMEOUT = 1800.0


BROWSER_USE_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "browser_use",
            "description": (
                "Playwright browser automation tool with action-based API. "
                "Actions: start, stop, open, navigate, navigate_back, screenshot, "
                "snapshot, click, type, eval, evaluate, resize, console_messages, "
                "handle_dialog, file_upload, fill_form, install, press_key, "
                "network_requests, run_code, drag, hover, select_option, tabs, "
                "wait_for, pdf, close."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "url": {"type": "string"},
                    "page_id": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "code": {"type": "string"},
                    "path": {"type": "string"},
                    "wait": {"type": "integer"},
                    "full_page": {"type": "boolean"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "level": {"type": "string"},
                    "filename": {"type": "string"},
                    "accept": {"type": "boolean"},
                    "prompt_text": {"type": "string"},
                    "ref": {"type": "string"},
                    "element": {"type": "string"},
                    "paths_json": {"type": "string"},
                    "fields_json": {"type": "string"},
                    "key": {"type": "string"},
                    "submit": {"type": "boolean"},
                    "slowly": {"type": "boolean"},
                    "include_static": {"type": "boolean"},
                    "screenshot_type": {"type": "string"},
                    "snapshot_filename": {"type": "string"},
                    "double_click": {"type": "boolean"},
                    "button": {"type": "string"},
                    "modifiers_json": {"type": "string"},
                    "start_ref": {"type": "string"},
                    "end_ref": {"type": "string"},
                    "start_selector": {"type": "string"},
                    "end_selector": {"type": "string"},
                    "start_element": {"type": "string"},
                    "end_element": {"type": "string"},
                    "values_json": {"type": "string"},
                    "tab_action": {"type": "string"},
                    "index": {"type": "integer"},
                    "wait_time": {"type": "number"},
                    "text_gone": {"type": "string"},
                    "frame_selector": {"type": "string"},
                    "headless": {"type": "boolean"},
                    "force_stop": {"type": "boolean"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }
]


_state: Dict[str, Any] = {
    "playwright": None,
    "browser": None,
    "context": None,
    "pages": {},
    "refs": {},
    "refs_frame": {},
    "console_logs": {},
    "network_requests": {},
    "pending_dialogs": {},
    "pending_file_choosers": {},
    "headless": True,
    "current_page_id": None,
    "page_counter": 0,
    "last_activity_time": 0.0,
    "_last_browser_error": None,
}


def _tool_response(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _touch_activity() -> None:
    _state["last_activity_time"] = time.monotonic()


def _get_default_headless() -> bool:
    """运行时读取 headless 默认值，以支持 .env 配置变更。"""
    return get_browser_headless_default()


def _is_bring_to_front_enabled() -> bool:
    """运行时读取 bring_to_front 开关配置。"""
    return get_browser_bring_to_front_enabled()


def _is_auto_stop_enabled() -> bool:
    """运行时读取 auto stop 开关配置。"""
    return get_browser_auto_stop_enabled()


def _bring_page_to_front(page) -> None:
    """尝试将目标页面置于前台（当浏览器可见时）。"""
    if (not _is_bring_to_front_enabled()) or page is None or _state.get("headless", True):
        return
    try:
        page.bring_to_front()
    except Exception:
        # 保持非致命错误，使常规自动化可以继续执行。
        pass


def _is_browser_running() -> bool:
    return _state.get("browser") is not None


def _chromium_launch_args() -> List[str]:
    args = get_browser_launch_args() or []
    if is_running_in_container():
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
    return args


def _parse_json_param(value: str, default: Any = None) -> Any:
    if not value or not isinstance(value, str):
        return default
    raw = value.strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if "," in raw:
            return [x.strip() for x in raw.split(",") if x.strip()]
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _reset_browser_state() -> None:
    _state["playwright"] = None
    _state["browser"] = None
    _state["context"] = None
    _state["pages"].clear()
    _state["refs"].clear()
    _state["refs_frame"].clear()
    _state["console_logs"].clear()
    _state["network_requests"].clear()
    _state["pending_dialogs"].clear()
    _state["pending_file_choosers"].clear()
    _state["current_page_id"] = None
    _state["page_counter"] = 0
    _state["last_activity_time"] = 0.0


def _cleanup_if_idle() -> None:
    if not _is_browser_running():
        return
    idle = time.monotonic() - _state.get("last_activity_time", 0.0)
    if idle >= _BROWSER_IDLE_TIMEOUT:
        if not _is_auto_stop_enabled():
            return
        logger.info("Browser idle for %.0fs, stopping", idle)
        _action_stop(force=True)


def _attach_page_listeners(page, page_id: str) -> None:
    logs = _state["console_logs"].setdefault(page_id, [])

    def on_console(msg):
        logs.append({"level": msg.type, "text": msg.text})

    requests_list = _state["network_requests"].setdefault(page_id, [])

    def on_request(req):
        requests_list.append(
            {
                "url": req.url,
                "method": req.method,
                "resourceType": getattr(req, "resource_type", None),
            }
        )

    def on_response(res):
        for req in requests_list:
            if req.get("url") == res.url and "status" not in req:
                req["status"] = res.status
                break

    dialogs = _state["pending_dialogs"].setdefault(page_id, [])

    def on_dialog(dialog):
        dialogs.append(dialog)

    choosers = _state["pending_file_choosers"].setdefault(page_id, [])

    def on_filechooser(chooser):
        choosers.append(chooser)

    page.on("console", on_console)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("dialog", on_dialog)
    page.on("filechooser", on_filechooser)


def _next_page_id() -> str:
    _state["page_counter"] = _state.get("page_counter", 0) + 1
    return f"page_{_state['page_counter']}"


def _attach_context_listeners(context) -> None:
    def on_page(page):
        new_id = _next_page_id()
        _state["refs"][new_id] = {}
        _state["console_logs"][new_id] = []
        _state["network_requests"][new_id] = []
        _state["pending_dialogs"][new_id] = []
        _state["pending_file_choosers"][new_id] = []
        _attach_page_listeners(page, new_id)
        _state["pages"][new_id] = page
        _state["current_page_id"] = new_id

    context.on("page", on_page)


def _launch_browser(headless: bool) -> tuple[Any, Any, Any]:
    pw = sync_playwright().start()
    use_sys_default = get_browser_use_sys_default()
    default_kind, default_path = (
        get_system_default_browser() if use_sys_default else (None, None)
    )


    launch_kwargs: Dict[str, Any] = {"headless": headless}
    extra_args = _chromium_launch_args()
    if extra_args:
        launch_kwargs["args"] = extra_args

    if use_sys_default:
        launch_kwargs["executable_path"] = default_path
        browser = pw.chromium.launch(**launch_kwargs)
    else:
        browser = pw.chromium.launch(**launch_kwargs)

    context = browser.new_context()
    _attach_context_listeners(context)
    return pw, browser, context


def _ensure_browser() -> bool:
    print("Ensuring browser is running...")
    _cleanup_if_idle()
    if _state["browser"] is not None and _state["context"] is not None:
        _touch_activity()
        return True

    # 隐式启动时（例如在 action=start 之前使用 action=open），
    # 从运行时配置同步 headless 设置。
    _state["headless"] = _get_default_headless()
    print(f"Browser not running, launching with headless={_state['headless']}...")
    try:
        pw, browser, context = _launch_browser(_state["headless"])
        _state["playwright"] = pw
        _state["browser"] = browser
        _state["context"] = context
        _state["_last_browser_error"] = None
        _touch_activity()
        return True
    except Exception as error:
        _state["_last_browser_error"] = str(error)
        return False


def _get_page(page_id: str):
    return _state["pages"].get(page_id)


def _get_refs(page_id: str) -> Dict[str, Dict[str, Any]]:
    return _state["refs"].setdefault(page_id, {})


def _get_root(page, frame_selector: str = ""):
    if frame_selector and frame_selector.strip():
        return page.frame_locator(frame_selector.strip())
    return page


def _get_locator_by_ref(page, page_id: str, ref: str, frame_selector: str = ""):
    refs = _get_refs(page_id)
    info = refs.get(ref)
    if not info:
        return None

    use_frame_selector = info.get("frame_selector") or frame_selector
    root = _get_root(page, use_frame_selector)
    selector = info.get("selector")
    if selector:
        return root.locator(selector).first

    role = info.get("role", "generic")
    name = info.get("name")
    nth = info.get("nth", 0)
    locator = root.get_by_role(role, name=name or None)
    if isinstance(nth, int) and nth > 0:
        locator = locator.nth(nth)
    return locator


def _infer_role(tag: str, input_type: str, role_attr: str) -> str:
    if role_attr:
        return role_attr
    tag = (tag or "").lower()
    input_type = (input_type or "").lower()
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag == "select":
        return "combobox"
    if tag == "textarea":
        return "textbox"
    if tag == "input":
        if input_type in ("submit", "button", "reset"):
            return "button"
        if input_type == "checkbox":
            return "checkbox"
        if input_type == "radio":
            return "radio"
        return "textbox"
    return "generic"


def _build_snapshot(page, frame_selector: str = "") -> tuple[str, Dict[str, Dict[str, Any]]]:
    root = _get_root(page, frame_selector)
    query = "a,button,input,select,textarea,[role],summary,[onclick],[tabindex]"
    locator = root.locator(query)
    count = min(locator.count(), 200)

    refs: Dict[str, Dict[str, Any]] = {}
    lines: List[str] = []
    # 用于跟踪名称冲突，格式: (clean_name, role) -> count
    name_counter: Dict[tuple[str, str], int] = {}

    # 批量处理元素：先筛选可见元素，再批量提取信息
    # 使用 :is() 伪类同时匹配 disabled 和 aria-disabled
    visible_locator = locator.filter(has_not=root.locator(":is([disabled], [aria-disabled='true'])"))
    visible_count = min(visible_locator.count(), 200)

    for idx in range(visible_count):
        item = visible_locator.nth(idx)
        try:
            # 检查位置和大小，确保元素真正可见
            box = item.bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1:
                continue

            # 单次 JS 调用获取所有信息
            info = item.evaluate("""
                el => {
                    const tag = el.tagName ? el.tagName.toLowerCase() : '';
                    const inputType = el.getAttribute('type') || '';
                    const roleAttr = el.getAttribute('role') || '';

                    // 获取名称
                    let name = el.getAttribute('aria-label') || el.innerText || (el.value || '') + '' || el.getAttribute('placeholder') || '';
                    name = name.trim().replace(/\\s+/g, ' ').slice(0, 120);

                    // 生成选择器
                    let selector = '';
                    const stableId = el.getAttribute('data-testid') || el.getAttribute('data-id');
                    if (stableId) {
                        selector = el.getAttribute('data-testid') ? `[data-testid="${stableId}"]` : `[data-id="${stableId}"]`;
                    } else if (el.id) {
                        selector = '#' + (window.CSS ? CSS.escape(el.id) : el.id);
                    } else {
                        const esc = (s) => window.CSS ? CSS.escape(s) : s;
                        let path = [];
                        let node = el;
                        while (node && node.nodeType === 1 && path.length < 6) {
                            let sel = node.tagName.toLowerCase();
                            if (node.className && typeof node.className === 'string') {
                                const cls = node.className.trim().split(/\\s+/).filter(Boolean).slice(0, 2);
                                if (cls.length) sel += '.' + cls.map(esc).join('.');
                            }
                            const parent = node.parentElement;
                            if (parent) {
                                const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
                                if (siblings.length > 1) sel += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                            }
                            path.unshift(sel);
                            node = parent;
                        }
                        selector = path.join(' > ');
                    }

                    const disabled = el.hasAttribute('disabled') || (tag === 'input' && el.getAttribute('readonly'));

                    return { tag, inputType, roleAttr, name, selector, disabled };
                }
            """)

            tag = info["tag"]
            input_type = info["inputType"]
            role_attr = info["roleAttr"]
            name = info["name"]
            selector = info["selector"]
            disabled = info["disabled"]

            if disabled:
                continue

            role = _infer_role(str(tag), str(input_type), str(role_attr))

            # 处理名称冲突：同一 role + name 组合使用索引区分
            name_key = (name, role)
            name_counter[name_key] = name_counter.get(name_key, 0) + 1
            name_idx = name_counter[name_key]

            ref = f"e{idx + 1}"
            clean_name = str(name or "").strip()
            clean_selector = str(selector or "").strip()

            refs[ref] = {
                "role": role,
                "name": clean_name,
                "selector": clean_selector,
                "nth": name_idx,
                "frame_selector": frame_selector.strip() if frame_selector else "",
                "tag": tag,
            }

            # 在输出中添加索引以区分同名元素
            display_name = clean_name if name_idx == 1 else f"{clean_name} ({name_idx})"
            lines.append(f'ref={ref} role={role} frame_selector="{frame_selector}" target="{display_name}" selector="{clean_selector}"')
        except PlaywrightError:
            continue

    if not lines:
        return "(no interactive elements found)", refs
    return "\n".join(lines), refs


def _action_start(headless: Optional[bool] = None) -> str:
    effective_headless = _get_default_headless() if headless is None else bool(headless)
    browser_exists = _state["browser"] is not None
    if browser_exists:
        if effective_headless != _state["headless"]:
            _action_stop()
        else:
            return _tool_response({"ok": True, "message": "Browser already running"})

    _state["headless"] = effective_headless
    try:
        pw, browser, context = _launch_browser(_state["headless"])
        _state["playwright"] = pw
        _state["browser"] = browser
        _state["context"] = context
        _touch_activity()
        message = "Browser started" if effective_headless else "Browser started (visible window)"
        return _tool_response({"ok": True, "message": message})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Browser start failed: {error!s}"})


def _action_stop(force: bool = False) -> str:
    if (not force) and (not _is_auto_stop_enabled()):
        return _tool_response(
            {
                "ok": True,
                "message": "Auto stop disabled by config; skip stop",
            }
        )

    if not _is_browser_running():
        return _tool_response({"ok": True, "message": "Browser not running"})

    try:
        if _state["browser"] is not None:
            _state["browser"].close()
        if _state["playwright"] is not None:
            _state["playwright"].stop()
        _reset_browser_state()
        return _tool_response({"ok": True, "message": "Browser stopped"})
    except Exception as error:
        _reset_browser_state()
        return _tool_response({"ok": False, "error": f"Browser stop failed: {error!s}"})


def _action_open(url: str, page_id: str) -> str:
    url = (url or "").strip()
    if not url:
        return _tool_response({"ok": False, "error": "url required for open"})
    if not _ensure_browser():
        err = _state.get("_last_browser_error") or "Browser not started"
        return _tool_response({"ok": False, "error": err})

    try:
        page = _state["context"].new_page()
        _state["refs"][page_id] = {}
        _state["console_logs"][page_id] = []
        _state["network_requests"][page_id] = []
        _state["pending_dialogs"][page_id] = []
        _state["pending_file_choosers"][page_id] = []
        _attach_page_listeners(page, page_id)
        page.goto(url)
        _bring_page_to_front(page)
        _state["pages"][page_id] = page
        _state["current_page_id"] = page_id
        _touch_activity()
        return _tool_response(
            {"ok": True, "message": f"Opened {url}", "page_id": page_id, "url": url}
        )
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Open failed: {error!s}"})


def _action_navigate(url: str, page_id: str) -> str:
    url = (url or "").strip()
    if not url:
        return _tool_response({"ok": False, "error": "url required for navigate"})
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.goto(url)
        _bring_page_to_front(page)
        _state["current_page_id"] = page_id
        _touch_activity()
        return _tool_response(
            {"ok": True, "message": f"Navigated to {url}", "url": page.url}
        )
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Navigate failed: {error!s}"})


def _action_navigate_back(page_id: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.go_back()
        _bring_page_to_front(page)
        _touch_activity()
        return _tool_response({"ok": True, "message": "Navigated back", "url": page.url})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Navigate back failed: {error!s}"})


def _action_screenshot(
    page_id: str,
    path: str,
    full_page: bool,
    screenshot_type: str,
    ref: str,
    frame_selector: str,
) -> str:
    output_path = (path or "").strip()
    ext = "jpeg" if screenshot_type == "jpeg" else "png"
    if not output_path:
        output_path = f"page-{int(time.time())}.{ext}"

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if ref and ref.strip():
            locator = _get_locator_by_ref(page, page_id, ref.strip(), frame_selector)
            if locator is None:
                return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
            locator.screenshot(path=output_path, type=ext)
        elif frame_selector and frame_selector.strip():
            _get_root(page, frame_selector).locator("body").first.screenshot(
                path=output_path,
                type=ext,
            )
        else:
            page.screenshot(path=output_path, full_page=full_page, type=ext)
        _touch_activity()
        return _tool_response(
            {
                "ok": True,
                "message": f"Screenshot saved to {output_path}",
                "path": output_path,
            }
        )
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Screenshot failed: {error!s}"})


def _action_snapshot(page_id: str, filename: str, frame_selector: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        snapshot, refs = _build_snapshot(page, frame_selector)
        _state["refs"][page_id] = refs
        _state["refs_frame"][page_id] = frame_selector.strip() if frame_selector else ""

        payload: Dict[str, Any] = {
            "ok": True,
            "snapshot": snapshot,
            "refs": list(refs.keys()),
            "url": page.url,
        }
        if frame_selector and frame_selector.strip():
            payload["frame_selector"] = frame_selector.strip()
        if filename and filename.strip():
            output_path = (WORKING_DIR / filename.strip()).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(snapshot, encoding="utf-8")
            payload["filename"] = str(output_path)
        _touch_activity()
        return _tool_response(payload)
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Snapshot failed: {error!s}"})


def _action_click(
    page_id: str,
    selector: str,
    ref: str,
    wait: int,
    double_click: bool,
    button: str,
    modifiers_json: str,
    frame_selector: str,
) -> str:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response({"ok": False, "error": "selector or ref required for click"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if wait > 0:
            time.sleep(wait / 1000.0)
        mods = _parse_json_param(modifiers_json, [])
        if not isinstance(mods, list):
            mods = []
        kwargs: Dict[str, Any] = {
            "button": button if button in ("left", "right", "middle") else "left"
        }
        valid_mods = [
            m
            for m in mods
            if m in ("Alt", "Control", "ControlOrMeta", "Meta", "Shift")
        ]
        if valid_mods:
            kwargs["modifiers"] = valid_mods

        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
        else:
            locator = _get_root(page, frame_selector).locator(selector).first

        if double_click:
            locator.dblclick(**kwargs)
        else:
            locator.click(**kwargs)
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Clicked {ref or selector}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Click failed: {error!s}"})


def _action_type(
    page_id: str,
    selector: str,
    ref: str,
    text: str,
    submit: bool,
    slowly: bool,
    frame_selector: str,
) -> str:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response({"ok": False, "error": "selector or ref required for type"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
        else:
            locator = _get_root(page, frame_selector).locator(selector).first

        if slowly and hasattr(locator, "press_sequentially"):
            locator.press_sequentially(text or "")
        else:
            locator.fill(text or "")
        if submit:
            locator.press("Enter")
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Typed into {ref or selector}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Type failed: {error!s}"})


def _safe_json_result(result: Any) -> str:
    try:
        return _tool_response({"ok": True, "result": result})
    except TypeError:
        return _tool_response({"ok": True, "result": str(result)})


def _action_eval(page_id: str, code: str) -> str:
    code = (code or "").strip()
    if not code:
        return _tool_response({"ok": False, "error": "code required for eval"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if code.startswith("(") or code.startswith("function"):
            result = page.evaluate(code)
        else:
            result = page.evaluate(f"() => {{ return ({code}); }}")
        _touch_activity()
        return _safe_json_result(result)
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Eval failed: {error!s}"})


def _action_evaluate(
    page_id: str,
    code: str,
    ref: str,
    frame_selector: str,
) -> str:
    code = (code or "").strip()
    if not code:
        return _tool_response({"ok": False, "error": "code required for evaluate"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if ref and ref.strip():
            locator = _get_locator_by_ref(page, page_id, ref.strip(), frame_selector)
            if locator is None:
                return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
            result = locator.evaluate(code)
        else:
            if code.startswith("(") or code.startswith("function"):
                result = page.evaluate(code)
            else:
                result = page.evaluate(f"() => {{ return ({code}); }}")
        _touch_activity()
        return _safe_json_result(result)
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Evaluate failed: {error!s}"})


def _action_resize(page_id: str, width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return _tool_response({"ok": False, "error": "width and height must be positive"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.set_viewport_size({"width": width, "height": height})
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Resized to {width}x{height}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Resize failed: {error!s}"})


def _action_console_messages(page_id: str, level: str, filename: str) -> str:
    level = (level or "info").strip().lower()
    order = ("error", "warning", "info", "debug", "log")
    idx = order.index(level) if level in order else 2

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    logs = _state["console_logs"].get(page_id, [])
    filtered = [m for m in logs if order.index(m["level"]) <= idx] if level in order else logs
    lines = [f"[{m['level']}] {m['text']}" for m in filtered]
    text = "\n".join(lines)

    if filename and filename.strip():
        output_path = (WORKING_DIR / filename.strip()).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        return _tool_response(
            {
                "ok": True,
                "message": f"Console messages saved to {output_path}",
                "filename": str(output_path),
            }
        )
    return _tool_response({"ok": True, "messages": filtered, "text": text})


def _action_handle_dialog(page_id: str, accept: bool, prompt_text: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    dialogs = _state["pending_dialogs"].get(page_id, [])
    if not dialogs:
        return _tool_response({"ok": False, "error": "No pending dialog"})

    try:
        dialog = dialogs.pop(0)
        if accept:
            if prompt_text:
                dialog.accept(prompt_text)
            else:
                dialog.accept()
        else:
            dialog.dismiss()
        _touch_activity()
        return _tool_response({"ok": True, "message": "Dialog handled"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Handle dialog failed: {error!s}"})


def _action_file_upload(page_id: str, paths_json: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    paths = _parse_json_param(paths_json, [])
    if not isinstance(paths, list):
        paths = []

    try:
        choosers = _state["pending_file_choosers"].get(page_id, [])
        if not choosers:
            return _tool_response(
                {
                    "ok": False,
                    "error": "No chooser. Click upload then file_upload.",
                }
            )
        chooser = choosers.pop(0)
        chooser.set_files(paths if paths else [])
        _touch_activity()
        if paths:
            return _tool_response({"ok": True, "message": f"Uploaded {len(paths)} file(s)"})
        return _tool_response({"ok": True, "message": "File chooser cancelled"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"File upload failed: {error!s}"})


def _action_fill_form(page_id: str, fields_json: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    fields = _parse_json_param(fields_json, [])
    if not isinstance(fields, list) or not fields:
        return _tool_response({"ok": False, "error": "fields required (JSON array)"})

    refs = _get_refs(page_id)
    frame = _state["refs_frame"].get(page_id, "")

    try:
        for field in fields:
            ref = str(field.get("ref", "")).strip()
            if not ref or ref not in refs:
                continue
            locator = _get_locator_by_ref(page, page_id, ref, frame)
            if locator is None:
                continue

            field_type = str(field.get("type", "textbox")).lower()
            value = field.get("value")
            if field_type == "checkbox":
                if isinstance(value, str):
                    value = value.strip().lower() in ("true", "1", "yes")
                locator.set_checked(bool(value))
            elif field_type == "radio":
                locator.set_checked(True)
            elif field_type == "combobox":
                locator.select_option(label=value if isinstance(value, str) else None, value=value)
            elif field_type == "slider":
                locator.fill(str(value))
            else:
                locator.fill(str(value) if value is not None else "")
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Filled {len(fields)} field(s)"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Fill form failed: {error!s}"})


def _run_playwright_install() -> None:
    subprocess.run(
        [sys.executable, "-m", "playwright", "install"],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _action_install() -> str:
    exe = get_playwright_chromium_executable_path()
    if exe:
        return _tool_response(
            {
                "ok": True,
                "message": f"Using system browser (no download): {exe}",
            }
        )

    if sys.platform == "darwin":
        return _tool_response(
            {
                "ok": True,
                "message": "On macOS using Safari (WebKit); no browser download needed.",
            }
        )

    try:
        _run_playwright_install()
        return _tool_response({"ok": True, "message": "Browser installed"})
    except subprocess.TimeoutExpired:
        return _tool_response(
            {
                "ok": False,
                "error": (
                    "Browser install timed out (10 min). Run manually in terminal: "
                    f"{sys.executable!s} -m playwright install"
                ),
            }
        )
    except Exception as error:
        return _tool_response(
            {
                "ok": False,
                "error": (
                    f"Install failed: {error!s}. Install manually: "
                    f"{sys.executable!s} -m pip install playwright && "
                    f"{sys.executable!s} -m playwright install"
                ),
            }
        )


def _action_press_key(page_id: str, key: str) -> str:
    key = (key or "").strip()
    if not key:
        return _tool_response({"ok": False, "error": "key required for press_key"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.keyboard.press(key)
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Pressed key {key}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Press key failed: {error!s}"})


def _action_network_requests(page_id: str, include_static: bool, filename: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    requests = _state["network_requests"].get(page_id, [])
    if not include_static:
        static = ("image", "stylesheet", "font", "media")
        requests = [r for r in requests if r.get("resourceType") not in static]

    lines = [
        f"{r.get('method', '')} {r.get('url', '')} {r.get('status', '')}" for r in requests
    ]
    text = "\n".join(lines)

    if filename and filename.strip():
        output_path = (WORKING_DIR / filename.strip()).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        return _tool_response(
            {
                "ok": True,
                "message": f"Network requests saved to {output_path}",
                "filename": str(output_path),
            }
        )

    return _tool_response({"ok": True, "requests": requests, "text": text})


def _action_run_code(page_id: str, code: str) -> str:
    return _action_eval(page_id, code)


def _action_drag(
    page_id: str,
    start_ref: str,
    end_ref: str,
    start_selector: str,
    end_selector: str,
    frame_selector: str,
) -> str:
    start_ref = (start_ref or "").strip()
    end_ref = (end_ref or "").strip()
    start_selector = (start_selector or "").strip()
    end_selector = (end_selector or "").strip()

    use_refs = bool(start_ref and end_ref)
    use_selectors = bool(start_selector and end_selector)
    if not use_refs and not use_selectors:
        return _tool_response(
            {
                "ok": False,
                "error": "drag needs (start_ref,end_ref) or (start_selector,end_selector)",
            }
        )

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if use_refs:
            start_locator = _get_locator_by_ref(page, page_id, start_ref, frame_selector)
            end_locator = _get_locator_by_ref(page, page_id, end_ref, frame_selector)
            if start_locator is None or end_locator is None:
                return _tool_response({"ok": False, "error": "Unknown ref for drag"})
        else:
            root = _get_root(page, frame_selector)
            start_locator = root.locator(start_selector).first
            end_locator = root.locator(end_selector).first

        start_locator.drag_to(end_locator)
        _touch_activity()
        return _tool_response({"ok": True, "message": "Drag completed"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Drag failed: {error!s}"})


def _action_hover(
    page_id: str,
    ref: str,
    selector: str,
    frame_selector: str,
) -> str:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response({"ok": False, "error": "hover requires ref or selector"})

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
        else:
            locator = _get_root(page, frame_selector).locator(selector).first
        locator.hover()
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Hovered {ref or selector}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Hover failed: {error!s}"})


def _action_select_option(
    page_id: str,
    ref: str,
    values_json: str,
    frame_selector: str,
) -> str:
    ref = (ref or "").strip()
    values = _parse_json_param(values_json, [])
    if not isinstance(values, list):
        values = [values] if values is not None else []

    if not ref:
        return _tool_response({"ok": False, "error": "ref required for select_option"})
    if not values:
        return _tool_response(
            {
                "ok": False,
                "error": "values required (JSON array or comma-separated)",
            }
        )

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
        if locator is None:
            return _tool_response({"ok": False, "error": f"Unknown ref: {ref}"})
        locator.select_option(value=values)
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Selected {values}"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Select option failed: {error!s}"})


def _action_tabs(page_id: str, tab_action: str, index: int) -> str:
    tab_action = (tab_action or "").strip().lower()
    if not tab_action:
        return _tool_response(
            {
                "ok": False,
                "error": "tab_action required (list, new, close, select)",
            }
        )

    pages = _state["pages"]
    page_ids = list(pages.keys())

    if tab_action == "list":
        return _tool_response({"ok": True, "tabs": page_ids, "count": len(page_ids)})

    if tab_action == "new":
        if not _state["context"] and not _ensure_browser():
            err = _state.get("_last_browser_error") or "Browser not started"
            return _tool_response({"ok": False, "error": err})

        try:
            page = _state["context"].new_page()
            new_id = _next_page_id()
            _state["refs"][new_id] = {}
            _state["console_logs"][new_id] = []
            _state["network_requests"][new_id] = []
            _state["pending_dialogs"][new_id] = []
            _state["pending_file_choosers"][new_id] = []
            _attach_page_listeners(page, new_id)
            _bring_page_to_front(page)
            _state["pages"][new_id] = page
            _state["current_page_id"] = new_id
            _touch_activity()
            return _tool_response(
                {"ok": True, "page_id": new_id, "tabs": list(_state["pages"].keys())}
            )
        except Exception as error:
            return _tool_response({"ok": False, "error": f"New tab failed: {error!s}"})

    if tab_action == "close":
        target_id = page_ids[index] if 0 <= index < len(page_ids) else page_id
        return _action_close(target_id)

    if tab_action == "select":
        target_id = page_ids[index] if 0 <= index < len(page_ids) else page_id
        if target_id not in _state["pages"]:
            return _tool_response({"ok": False, "error": f"Page '{target_id}' not found"})
        _bring_page_to_front(_state["pages"].get(target_id))
        _state["current_page_id"] = target_id
        return _tool_response(
            {
                "ok": True,
                "message": f"Use page_id={target_id} for later actions",
                "page_id": target_id,
            }
        )

    return _tool_response({"ok": False, "error": f"Unknown tab_action: {tab_action}"})


def _action_wait_for(page_id: str, wait_time: float, text: str, text_gone: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        if wait_time and wait_time > 0:
            time.sleep(wait_time)

        text = (text or "").strip()
        text_gone = (text_gone or "").strip()

        if text:
            page.get_by_text(text).first.wait_for(state="visible", timeout=30000)
        if text_gone:
            page.get_by_text(text_gone).first.wait_for(state="hidden", timeout=30000)

        _touch_activity()
        return _tool_response({"ok": True, "message": "Wait completed"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Wait failed: {error!s}"})


def _action_pdf(page_id: str, path: str) -> str:
    output_path = (path or "page.pdf").strip() or "page.pdf"

    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.pdf(path=output_path)
        _touch_activity()
        return _tool_response(
            {"ok": True, "message": f"PDF saved to {output_path}", "path": output_path}
        )
    except Exception as error:
        return _tool_response({"ok": False, "error": f"PDF failed: {error!s}"})


def _action_close(page_id: str) -> str:
    page = _get_page(page_id)
    if not page:
        return _tool_response({"ok": False, "error": f"Page '{page_id}' not found"})

    try:
        page.close()
        del _state["pages"][page_id]
        for key in (
            "refs",
            "refs_frame",
            "console_logs",
            "network_requests",
            "pending_dialogs",
            "pending_file_choosers",
        ):
            _state[key].pop(page_id, None)
        if _state.get("current_page_id") == page_id:
            remaining = list(_state["pages"].keys())
            _state["current_page_id"] = remaining[0] if remaining else None
        _touch_activity()
        return _tool_response({"ok": True, "message": f"Closed page '{page_id}'"})
    except Exception as error:
        return _tool_response({"ok": False, "error": f"Close failed: {error!s}"})


def browser_use_tool(
    action: str,
    url: str = "",
    page_id: str = "default",
    selector: str = "",
    text: str = "",
    code: str = "",
    path: str = "",
    wait: int = 0,
    full_page: bool = False,
    width: int = 0,
    height: int = 0,
    level: str = "info",
    filename: str = "",
    accept: bool = True,
    prompt_text: str = "",
    ref: str = "",
    element: str = "",
    paths_json: str = "",
    fields_json: str = "",
    key: str = "",
    submit: bool = False,
    slowly: bool = False,
    include_static: bool = False,
    screenshot_type: str = "png",
    snapshot_filename: str = "",
    double_click: bool = False,
    button: str = "left",
    modifiers_json: str = "",
    start_ref: str = "",
    end_ref: str = "",
    start_selector: str = "",
    end_selector: str = "",
    start_element: str = "",
    end_element: str = "",
    values_json: str = "",
    tab_action: str = "",
    index: int = -1,
    wait_time: float = 0,
    text_gone: str = "",
    frame_selector: str = "",
    headless: Optional[bool] = None,
    force_stop: bool = False,
) -> str:
    del element, start_element, end_element

    action = (action or "").strip().lower()
    if not action:
        return _tool_response({"ok": False, "error": "action required"})

    page_id = (page_id or "default").strip() or "default"
    current = _state.get("current_page_id")
    pages = _state.get("pages") or {}
    if page_id == "default" and current and current in pages:
        page_id = current

    try:
        if action == "start":
            resolved_headless = None if headless is None else _coerce_bool(headless, _get_default_headless())
            return _action_start(headless=resolved_headless)
        if action == "stop":
            return _action_stop(force=_coerce_bool(force_stop, False))
        if action == "open":
            return _action_open(url, page_id)
        if action == "navigate":
            return _action_navigate(url, page_id)
        if action == "navigate_back":
            return _action_navigate_back(page_id)
        if action in ("screenshot", "take_screenshot"):
            return _action_screenshot(
                page_id,
                path or filename,
                _coerce_bool(full_page, False),
                (screenshot_type or "png").strip().lower(),
                ref,
                frame_selector,
            )
        if action == "snapshot":
            return _action_snapshot(page_id, snapshot_filename or filename, frame_selector)
        if action == "click":
            return _action_click(
                page_id,
                selector,
                ref,
                _coerce_int(wait, 0),
                _coerce_bool(double_click, False),
                button,
                modifiers_json,
                frame_selector,
            )
        if action == "type":
            return _action_type(
                page_id,
                selector,
                ref,
                text,
                _coerce_bool(submit, False),
                _coerce_bool(slowly, False),
                frame_selector,
            )
        if action == "eval":
            return _action_eval(page_id, code)
        if action == "evaluate":
            return _action_evaluate(page_id, code, ref, frame_selector)
        if action == "resize":
            return _action_resize(page_id, _coerce_int(width, 0), _coerce_int(height, 0))
        if action == "console_messages":
            return _action_console_messages(page_id, level, filename or path)
        if action == "handle_dialog":
            return _action_handle_dialog(page_id, _coerce_bool(accept, True), prompt_text)
        if action == "file_upload":
            return _action_file_upload(page_id, paths_json)
        if action == "fill_form":
            return _action_fill_form(page_id, fields_json)
        if action == "install":
            return _action_install()
        if action == "press_key":
            return _action_press_key(page_id, key)
        if action == "network_requests":
            return _action_network_requests(page_id, _coerce_bool(include_static, False), filename or path)
        if action == "run_code":
            return _action_run_code(page_id, code)
        if action == "drag":
            return _action_drag(
                page_id,
                start_ref,
                end_ref,
                start_selector,
                end_selector,
                frame_selector,
            )
        if action == "hover":
            return _action_hover(page_id, ref, selector, frame_selector)
        if action == "select_option":
            return _action_select_option(page_id, ref, values_json, frame_selector)
        if action == "tabs":
            return _action_tabs(page_id, tab_action, _coerce_int(index, -1))
        if action == "wait_for":
            return _action_wait_for(page_id, _coerce_float(wait_time, 0.0), text, text_gone)
        if action == "pdf":
            return _action_pdf(page_id, path)
        if action == "close":
            return _action_close(page_id)
        return _tool_response({"ok": False, "error": f"Unknown action: {action}"})
    except Exception as error:
        logger.error("Browser tool error: %s", error, exc_info=True)
        return _tool_response({"ok": False, "error": str(error)})


def dispatch_browser_use_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name != "browser_use":
        return f"Error: Unknown tool `{tool_name}`."

    try:
        return browser_use_tool(
            action=str(arguments.get("action", "")),
            url=str(arguments.get("url", "")),
            page_id=str(arguments.get("page_id", "default")),
            selector=str(arguments.get("selector", "")),
            text=str(arguments.get("text", "")),
            code=str(arguments.get("code", "")),
            path=str(arguments.get("path", "")),
            wait=arguments.get("wait", 0),
            full_page=arguments.get("full_page", False),
            width=arguments.get("width", 0),
            height=arguments.get("height", 0),
            level=str(arguments.get("level", "info")),
            filename=str(arguments.get("filename", "")),
            accept=arguments.get("accept", True),
            prompt_text=str(arguments.get("prompt_text", "")),
            ref=str(arguments.get("ref", "")),
            element=str(arguments.get("element", "")),
            paths_json=str(arguments.get("paths_json", "")),
            fields_json=str(arguments.get("fields_json", "")),
            key=str(arguments.get("key", "")),
            submit=arguments.get("submit", False),
            slowly=arguments.get("slowly", False),
            include_static=arguments.get("include_static", False),
            screenshot_type=str(arguments.get("screenshot_type", "png")),
            snapshot_filename=str(arguments.get("snapshot_filename", "")),
            double_click=arguments.get("double_click", False),
            button=str(arguments.get("button", "left")),
            modifiers_json=str(arguments.get("modifiers_json", "")),
            start_ref=str(arguments.get("start_ref", "")),
            end_ref=str(arguments.get("end_ref", "")),
            start_selector=str(arguments.get("start_selector", "")),
            end_selector=str(arguments.get("end_selector", "")),
            start_element=str(arguments.get("start_element", "")),
            end_element=str(arguments.get("end_element", "")),
            values_json=str(arguments.get("values_json", "")),
            tab_action=str(arguments.get("tab_action", "")),
            index=arguments.get("index", -1),
            wait_time=arguments.get("wait_time", 0.0),
            text_gone=str(arguments.get("text_gone", "")),
            frame_selector=str(arguments.get("frame_selector", "")),
            headless=arguments.get("headless"),
            force_stop=arguments.get("force_stop", False),
        )
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\n{error}"
