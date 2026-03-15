import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# 配置日志
logger = logging.getLogger(__name__)

WORKING_DIR = Path.cwd()
COMMAND_OUTPUT_MAX_CHARS = 12000


BASH_EXEC_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_bash_command",
            "description": "Run a bash command and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory. Defaults to current working directory.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout in seconds. Defaults to 60.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
]


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


def run_bash_command_tool(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: Optional[int] = 60,
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

    bash_path = "/bin/bash" if Path("/bin/bash").exists() else "bash"
    shell_command = [bash_path, "-lc", command]

    logger.info(
        "run_bash_command: cwd=%s, timeout=%ds, command=%s",
        resolved_cwd,
        timeout_seconds,
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
            "run_bash_command timed out: command=%s, timeout=%ds",
            command,
            timeout_seconds,
        )
        return (
            "Error: Bash command execution timed out.\n"
            f"cwd={resolved_cwd}\n"
            f"timeout_seconds={timeout_seconds}\n"
            f"partial_stdout={_truncate_output(error.stdout or '')}\n"
            f"partial_stderr={_truncate_output(error.stderr or '')}"
        )
    except Exception as error:
        logger.error("run_bash_command failed: command=%s, error=%s", command, error)
        return f"Error: Run bash command failed due to\n{error}"

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
        f"runner={' '.join(shell_command[:2])}\n"
        f"exit_code={completed.returncode}\n"
        f"stdout:\n{stdout_text}\n\n"
        f"stderr:\n{stderr_text}"
    )


def dispatch_bash_exec_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        if tool_name == "run_bash_command":
            return run_bash_command_tool(
                command=str(arguments.get("command", "")),
                cwd=arguments.get("cwd"),
                timeout_seconds=arguments.get("timeout_seconds", 60),
            )
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\n{error}"

    return f"Error: Unknown tool `{tool_name}`."