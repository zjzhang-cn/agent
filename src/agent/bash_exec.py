import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 配置日志
logger = logging.getLogger(__name__)

WORKING_DIR = Path.cwd()
COMMAND_OUTPUT_MAX_CHARS = 12000


def _build_shell_tool_parameters() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Command to execute.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory. Defaults to current working directory.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Optional timeout in seconds. Defaults to 60.",
            },
            "shell": {
                "type": "string",
                "description": (
                    "Optional shell runner. Supported values: auto, bash, powershell, cmd. "
                    "Default is auto (Windows -> powershell, others -> bash)."
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }


def _build_shell_tool_definition(name: str, description: str) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _build_shell_tool_parameters(),
        },
    }


BASH_EXEC_TOOLS: List[Dict[str, Any]] = [
    _build_shell_tool_definition(
        "run_bash_command",
        "Run a shell command and return stdout/stderr. Backward-compatible name.",
    ),
    _build_shell_tool_definition(
        "run_shell_command",
        "Run a shell command and return stdout/stderr with cross-platform shell support.",
    ),
]


_SUPPORTED_SHELLS = {"auto", "bash", "powershell", "cmd"}


def _resolve_shell_kind(shell: str) -> str:
    if shell == "auto":
        return "powershell" if os.name == "nt" else "bash"
    return shell


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path
    return (WORKING_DIR / path).resolve()


def _truncate_output(text: str, max_chars: int = COMMAND_OUTPUT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text

    remaining = len(text) - max_chars
    return (
        f"{text[:max_chars]}\n\n"
        f"[output truncated: {remaining} more characters.]"
    )


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _normalize_shell_name(shell: Optional[str]) -> str:
    if shell is None:
        return "auto"

    normalized = str(shell).strip().lower()
    if not normalized:
        return "auto"

    aliases = {
        "sh": "bash",
        "pwsh": "powershell",
        "ps": "powershell",
        "powershell.exe": "powershell",
        "cmd.exe": "cmd",
    }
    return aliases.get(normalized, normalized)


def _resolve_shell_runner(shell: str) -> Tuple[List[str], str]:
    resolved_shell = _resolve_shell_kind(shell)

    if resolved_shell == "bash":
        bash_candidate = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
        if not bash_candidate:
            raise FileNotFoundError("Bash executable not found.")
        return [str(bash_candidate), "-lc"], "bash"

    if resolved_shell == "powershell":
        powershell_candidate = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell_candidate:
            raise FileNotFoundError(
                "PowerShell executable not found (tried pwsh and powershell)."
            )
        return [
            str(powershell_candidate),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
        ], "powershell"

    if resolved_shell == "cmd":
        cmd_candidate = os.environ.get("COMSPEC") or shutil.which("cmd")
        if not cmd_candidate:
            raise FileNotFoundError("cmd executable not found.")
        return [str(cmd_candidate), "/d", "/s", "/c"], "cmd"

    raise ValueError(
        f"Unsupported shell {shell!r}. Supported values: {', '.join(sorted(_SUPPORTED_SHELLS))}."
    )


def get_effective_shell_name(shell: Optional[str] = None) -> str:
    """获取用于日志记录/显示的有效 Shell 类型。

    返回实际将使用的 Shell 类型（例如：Windows 上 auto -> powershell，
    类 Unix 系统上 auto -> bash），不检查可执行文件是否存在。
    """
    requested_shell = _normalize_shell_name(shell)
    if requested_shell not in _SUPPORTED_SHELLS:
        return requested_shell
    return _resolve_shell_kind(requested_shell)


def run_bash_command_tool(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: Optional[int] = 60,
    shell: Optional[str] = None,
) -> str:
    if not command:
        return "Error: No `command` provided."

    resolved_cwd = WORKING_DIR
    if cwd:
        resolved_cwd = _resolve_path(cwd)
        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            return f"Error: cwd {resolved_cwd} is not an existing directory."

    if timeout_seconds is None:
        timeout_seconds = 60
    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        return f"Error: timeout_seconds must be an integer, got {timeout_seconds!r}."

    if timeout_seconds <= 0:
        return "Error: timeout_seconds must be greater than 0."

    requested_shell = _normalize_shell_name(shell)
    if requested_shell not in _SUPPORTED_SHELLS:
        supported = ", ".join(sorted(_SUPPORTED_SHELLS))
        return f"Error: Unsupported shell {shell!r}. Supported values: {supported}."

    try:
        runner_prefix, resolved_shell = _resolve_shell_runner(requested_shell)
    except (FileNotFoundError, ValueError) as error:
        return f"Error: {error}"

    shell_command = [*runner_prefix, command]

    logger.info(
        "run_bash_command: cwd=%s, timeout=%ds, shell=%s, resolved=%s, command=%s",
        resolved_cwd,
        timeout_seconds,
        requested_shell,
        resolved_shell,
        command,
    )

    try:
        completed = subprocess.run(
            shell_command,
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        logger.error(
            "run_bash_command timed out: shell=%s, command=%s, timeout=%ds",
            resolved_shell,
            command,
            timeout_seconds,
        )
        return (
            "Error: Shell command execution timed out.\n"
            f"cwd={resolved_cwd}\n"
            f"timeout_seconds={timeout_seconds}\n"
            f"partial_stdout={_truncate_output(_to_text(error.stdout))}\n"
            f"partial_stderr={_truncate_output(_to_text(error.stderr))}"
        )
    except Exception as error:
        logger.error("run_bash_command failed: command=%s, error=%s", command, error)
        return f"Error: Run shell command failed due to\n{error}"

    stdout_text = _truncate_output(completed.stdout or "")
    stderr_text = _truncate_output(completed.stderr or "")

    logger.info(
        "run_bash_command completed: exit_code=%d, stdout_len=%d, stderr_len=%d",
        completed.returncode,
        len(completed.stdout or ""),
        len(completed.stderr or ""),
    )

    return (
        f"cwd={resolved_cwd}\n"
        f"command={command}\n"
        f"requested_shell={requested_shell}\n"
        f"resolved_shell={resolved_shell}\n"
        f"runner={' '.join(runner_prefix)}\n"
        f"exit_code={completed.returncode}\n"
        f"stdout:\n{stdout_text}\n\n"
        f"stderr:\n{stderr_text}"
    )


def dispatch_bash_exec_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        if tool_name in ("run_bash_command", "run_shell_command"):
            return run_bash_command_tool(
                command=str(arguments.get("command", "")),
                cwd=arguments.get("cwd"),
                timeout_seconds=arguments.get("timeout_seconds", 60),
                shell=arguments.get("shell"),
            )
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\n{error}"

    return f"Error: Unknown tool `{tool_name}`."