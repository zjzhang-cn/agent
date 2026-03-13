import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKING_DIR = Path.cwd()
SCRIPT_OUTPUT_MAX_CHARS = 12000


PYTHON_EXEC_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_python_script",
            "description": "Run a Python script file and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "Path to .py script, absolute or relative to working directory.",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional command-line arguments for the script.",
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
                "required": ["script_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python_code",
            "description": "Run Python code string and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code snippet to execute.",
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
                "required": ["code"],
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


def _truncate_output(text: str, max_chars: int = SCRIPT_OUTPUT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text

    remaining = len(text) - max_chars
    return (
        f"{text[:max_chars]}\n\n"
        f"[output truncated: {remaining} more characters.]"
    )


def run_python_script_tool(
    script_path: str,
    args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    timeout_seconds: Optional[int] = 60,
) -> str:
    if not script_path:
        return "Error: No `script_path` provided."

    resolved_script = _resolve_path(script_path)
    if not resolved_script.exists():
        return f"Error: Script {resolved_script} does not exist."
    if not resolved_script.is_file():
        return f"Error: Path {resolved_script} is not a file."
    if resolved_script.suffix.lower() != ".py":
        return f"Error: Script {resolved_script} is not a .py file."

    resolved_cwd = WORKING_DIR
    if cwd:
        resolved_cwd = _resolve_path(cwd)
        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            return f"Error: cwd {resolved_cwd} is not an existing directory."

    normalized_args: List[str] = []
    if args is not None:
        if not isinstance(args, list):
            return "Error: `args` must be a list of strings."
        for item in args:
            normalized_args.append(str(item))

    if timeout_seconds is None:
        timeout_seconds = 60
    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        return f"Error: timeout_seconds must be an integer, got {timeout_seconds!r}."

    if timeout_seconds <= 0:
        return "Error: timeout_seconds must be greater than 0."

    command = [sys.executable, str(resolved_script), *normalized_args]

    try:
        completed = subprocess.run(
            command,
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return (
            "Error: Python script execution timed out.\n"
            f"script={resolved_script}\n"
            f"timeout_seconds={timeout_seconds}\n"
            f"partial_stdout={_truncate_output(error.stdout or '')}\n"
            f"partial_stderr={_truncate_output(error.stderr or '')}"
        )
    except Exception as error:
        return f"Error: Run python script failed due to\n{error}"

    stdout_text = _truncate_output(completed.stdout or "")
    stderr_text = _truncate_output(completed.stderr or "")
    command_text = " ".join(command)

    return (
        f"script={resolved_script}\n"
        f"cwd={resolved_cwd}\n"
        f"command={command_text}\n"
        f"exit_code={completed.returncode}\n"
        f"stdout:\n{stdout_text}\n\n"
        f"stderr:\n{stderr_text}"
    )


def run_python_code_tool(
    code: str,
    cwd: Optional[str] = None,
    timeout_seconds: Optional[int] = 60,
) -> str:
    if not code:
        return "Error: No `code` provided."

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

    command = [sys.executable, "-c", code]

    try:
        completed = subprocess.run(
            command,
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return (
            "Error: Python code execution timed out.\n"
            f"timeout_seconds={timeout_seconds}\n"
            f"partial_stdout={_truncate_output(error.stdout or '')}\n"
            f"partial_stderr={_truncate_output(error.stderr or '')}"
        )
    except Exception as error:
        return f"Error: Run python code failed due to\n{error}"

    stdout_text = _truncate_output(completed.stdout or "")
    stderr_text = _truncate_output(completed.stderr or "")
    command_text = " ".join(command)

    return (
        f"cwd={resolved_cwd}\n"
        f"command={command_text}\n"
        f"exit_code={completed.returncode}\n"
        f"stdout:\n{stdout_text}\n\n"
        f"stderr:\n{stderr_text}"
    )


def dispatch_python_exec_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        if tool_name == "run_python_script":
            return run_python_script_tool(
                script_path=str(arguments.get("script_path", "")),
                args=arguments.get("args"),
                cwd=arguments.get("cwd"),
                timeout_seconds=arguments.get("timeout_seconds", 60),
            )
        if tool_name == "run_python_code":
            return run_python_code_tool(
                code=str(arguments.get("code", "")),
                cwd=arguments.get("cwd"),
                timeout_seconds=arguments.get("timeout_seconds", 60),
            )
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\n{error}"

    return f"Error: Unknown tool `{tool_name}`."
