import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv


def load_environment() -> None:
    env_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_path if env_path.exists() else None, override=False)


def get_config_value(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default

    lowered_value = value.strip().lower()
    if lowered_value in ("1", "true", "yes", "on"):
        return True
    if lowered_value in ("0", "false", "no", "off"):
        return False
    return default


def parse_config_value(value: Optional[str]) -> Optional[Any]:
    if value is None:
        return None

    stripped_value = value.strip()
    if not stripped_value:
        return None

    lowered_value = stripped_value.lower()
    if lowered_value == "true":
        return True
    if lowered_value == "false":
        return False

    try:
        return json.loads(stripped_value)
    except json.JSONDecodeError:
        return stripped_value


def parse_string_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None

    stripped_value = value.strip()
    if not stripped_value:
        return None

    try:
        parsed = json.loads(stripped_value)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        stripped_value = parsed.strip()

    return [item.strip() for item in stripped_value.split(",") if item.strip()]


def parse_positive_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None

    stripped_value = value.strip()
    if not stripped_value:
        return None

    try:
        parsed = int(stripped_value)
    except ValueError:
        return None

    return parsed if parsed > 0 else None


def is_running_in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True

    cgroup_paths = [Path("/proc/1/cgroup"), Path("/proc/self/cgroup")]
    for cgroup_path in cgroup_paths:
        if not cgroup_path.exists():
            continue
        try:
            text = cgroup_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in ("docker", "containerd", "kubepods", "podman")):
            return True

    return False


def get_playwright_chromium_executable_path() -> Optional[str]:
    env_keys = (
        "COPAW_CHROMIUM_EXECUTABLE",
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH",
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
        "CHROME_PATH",
    )
    for key in env_keys:
        value = get_config_value(key)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.exists():
            return str(candidate)

    command_candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
        "chrome",
    ]
    for command in command_candidates:
        resolved = shutil.which(command)
        if resolved:
            return resolved

    mac_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for raw_path in mac_paths:
        candidate = Path(raw_path)
        if candidate.exists():
            return str(candidate)

    return None


def get_system_default_browser() -> tuple[Optional[str], Optional[str]]:
    chromium_path = get_playwright_chromium_executable_path()
    if chromium_path:
        return "chromium", chromium_path

    if sys.platform == "darwin":
        safari_app = Path("/Applications/Safari.app")
        if safari_app.exists():
            return "webkit", str(safari_app)

    return None, None

def get_browser_launch_args() -> Optional[List[str]]:
    """返回 browser_use 启动浏览器时的额外参数列表。

    环境变量（按优先级依次检查）：
    - COPAW_BROWSER_LAUNCH_ARGS
    - BROWSER_LAUNCH_ARG
    
    """
    value = get_config_value("COPAW_BROWSER_LAUNCH_ARGS", "BROWSER_LAUNCH_ARG")
    if value is None:
        return None
    return parse_string_list(value) or None

def get_browser_use_sys_default() -> bool:
    """返回 browser_use 是否默认使用系统浏览器。

    环境变量（按优先级依次检查）：
    - COPAW_BROWSER_USE_SYS_DEFAULT
    - BROWSER_USE_SYS_DEFAULT
    """
    value = get_config_value(
        "COPAW_BROWSER_USE_SYS_DEFAULT",
        "BROWSER_USE_SYS_DEFAULT",
    )
    return parse_bool(value, default=True)
def get_browser_headless_default() -> bool:
    """返回浏览器启动时的默认 headless 模式。

    环境变量（按优先级依次检查）：
    - COPAW_BROWSER_HEADLESS
    - BROWSER_HEADLESS
    """
    value = get_config_value("COPAW_BROWSER_HEADLESS", "BROWSER_HEADLESS")
    return parse_bool(value, default=True)


def get_browser_bring_to_front_enabled() -> bool:
    """返回是否允许 browser_use 调用 page.bring_to_front()。

    环境变量（按优先级依次检查）：
    - COPAW_BROWSER_BRING_TO_FRONT
    - BROWSER_BRING_TO_FRONT
    """
    value = get_config_value(
        "COPAW_BROWSER_BRING_TO_FRONT",
        "BROWSER_BRING_TO_FRONT",
    )
    return parse_bool(value, default=True)


def get_browser_auto_stop_enabled() -> bool:
    """返回是否允许 browser_use 执行普通 stop 操作。

    环境变量（按优先级依次检查）：
    - COPAW_BROWSER_AUTO_STOP
    - BROWSER_AUTO_STOP
    """
    value = get_config_value(
        "COPAW_BROWSER_AUTO_STOP",
        "BROWSER_AUTO_STOP",
    )
    return parse_bool(value, default=True)
