from pathlib import Path
from typing import Any, Dict, List, Optional

WORKING_DIR = Path.cwd()
FILE_READ_MAX_CHARS = 12000


FILE_IO_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file. Use start_line/end_line (1-based, inclusive) for ranged reads.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, inclusive).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive).",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write.",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace all occurrences of old_text with new_text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["file_path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Append content to the end of a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append.",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        },
    },
]


def _resolve_file_path(file_path: str) -> Path:
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path
    return (WORKING_DIR / path).resolve()


def _truncate_text(text: str, max_chars: int = FILE_READ_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text

    remaining = len(text) - max_chars
    return (
        f"{text[:max_chars]}\\n\\n"
        f"[output truncated: {remaining} more characters. Narrow the range with start_line/end_line.]"
    )


def read_file_tool(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    if not file_path:
        return "Error: No `file_path` provided."

    if start_line is not None:
        try:
            start_line = int(start_line)
        except (ValueError, TypeError):
            return f"Error: start_line must be an integer, got {start_line!r}."

    if end_line is not None:
        try:
            end_line = int(end_line)
        except (ValueError, TypeError):
            return f"Error: end_line must be an integer, got {end_line!r}."

    resolved_path = _resolve_file_path(file_path)
    if not resolved_path.exists():
        return f"Error: The file {resolved_path} does not exist."
    if not resolved_path.is_file():
        return f"Error: The path {resolved_path} is not a file."

    try:
        content = resolved_path.read_text(encoding="utf-8")
    except Exception as error:
        return f"Error: Read file failed due to\\n{error}"

    all_lines = content.split("\\n")
    total = len(all_lines)
    s = max(1, start_line if start_line is not None else 1)
    e = min(total, end_line if end_line is not None else total)

    if s > total:
        return f"Error: start_line {s} exceeds file length ({total} lines)."
    if s > e:
        return f"Error: start_line ({s}) > end_line ({e})."

    selected = "\\n".join(all_lines[s - 1 : e])
    selected = _truncate_text(selected)

    if e < total and "[output truncated:" not in selected:
        remaining = total - e
        return (
            f"{resolved_path} (lines {s}-{e} of {total})\\n{selected}\\n\\n"
            f"[{remaining} more lines. Use start_line={e + 1} to continue.]"
        )

    return selected


def write_file_tool(file_path: str, content: str) -> str:
    if not file_path:
        return "Error: No `file_path` provided."

    resolved_path = _resolve_file_path(file_path)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(content, encoding="utf-8")
    except Exception as error:
        return f"Error: Write file failed due to\\n{error}"

    return f"Wrote {len(content)} bytes to {resolved_path}."


def edit_file_tool(file_path: str, old_text: str, new_text: str) -> str:
    if not file_path:
        return "Error: No `file_path` provided."

    resolved_path = _resolve_file_path(file_path)
    if not resolved_path.exists():
        return f"Error: The file {resolved_path} does not exist."
    if not resolved_path.is_file():
        return f"Error: The path {resolved_path} is not a file."

    try:
        content = resolved_path.read_text(encoding="utf-8")
    except Exception as error:
        return f"Error: Read file failed due to\\n{error}"

    if old_text not in content:
        return f"Error: The text to replace was not found in {resolved_path}."

    new_content = content.replace(old_text, new_text)
    return write_file_tool(str(resolved_path), new_content)


def append_file_tool(file_path: str, content: str) -> str:
    if not file_path:
        return "Error: No `file_path` provided."

    resolved_path = _resolve_file_path(file_path)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved_path, "a", encoding="utf-8") as file:
            file.write(content)
    except Exception as error:
        return f"Error: Append file failed due to\\n{error}"

    return f"Appended {len(content)} bytes to {resolved_path}."


def dispatch_file_io_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        if tool_name == "read_file":
            return read_file_tool(
                file_path=str(arguments.get("file_path", "")),
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
        if tool_name == "write_file":
            return write_file_tool(
                file_path=str(arguments.get("file_path", "")),
                content=str(arguments.get("content", "")),
            )
        if tool_name == "edit_file":
            return edit_file_tool(
                file_path=str(arguments.get("file_path", "")),
                old_text=str(arguments.get("old_text", "")),
                new_text=str(arguments.get("new_text", "")),
            )
        if tool_name == "append_file":
            return append_file_tool(
                file_path=str(arguments.get("file_path", "")),
                content=str(arguments.get("content", "")),
            )
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\\n{error}"

    return f"Error: Unknown tool `{tool_name}`."
