import shutil
from pathlib import Path
from typing import Any, Dict, List

WORKING_DIR = Path.cwd()


DIR_IO_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries in a directory. Adds '/' suffix for sub-directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    }
                },
                "required": ["dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory recursively (mkdir -p behavior).",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    }
                },
                "required": ["dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_directory",
            "description": "Remove an empty directory or recursively remove directory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "When true, delete all children with the directory.",
                    },
                },
                "required": ["dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_directory",
            "description": "Move or rename a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src_dir_path": {
                        "type": "string",
                        "description": "Source directory path.",
                    },
                    "dst_dir_path": {
                        "type": "string",
                        "description": "Destination directory path.",
                    },
                },
                "required": ["src_dir_path", "dst_dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_directory",
            "description": "Copy a directory tree from source to destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src_dir_path": {
                        "type": "string",
                        "description": "Source directory path.",
                    },
                    "dst_dir_path": {
                        "type": "string",
                        "description": "Destination directory path.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "When true, allows copying into existing destination directory.",
                    },
                },
                "required": ["src_dir_path", "dst_dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_exists",
            "description": "Check whether a path exists and whether it is a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to current working directory.",
                    }
                },
                "required": ["dir_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_working_directory",
            "description": "Get current working directory for relative path resolution.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def _resolve_dir_path(dir_path: str) -> Path:
    path = Path(dir_path).expanduser()
    if path.is_absolute():
        return path
    return (WORKING_DIR / path).resolve()


def list_directory_tool(dir_path: str) -> str:
    if not dir_path:
        return "Error: No `dir_path` provided."

    resolved_path = _resolve_dir_path(dir_path)
    if not resolved_path.exists():
        return f"Error: The directory {resolved_path} does not exist."
    if not resolved_path.is_dir():
        return f"Error: The path {resolved_path} is not a directory."

    try:
        entries = sorted(resolved_path.iterdir(), key=lambda p: p.name.lower())
    except Exception as error:
        return f"Error: List directory failed due to\\n{error}"

    if not entries:
        return f"{resolved_path} is empty."

    rendered = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries]
    return f"{resolved_path}\\n" + "\\n".join(rendered)


def create_directory_tool(dir_path: str) -> str:
    if not dir_path:
        return "Error: No `dir_path` provided."

    resolved_path = _resolve_dir_path(dir_path)
    try:
        resolved_path.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        return f"Error: Create directory failed due to\\n{error}"

    return f"Created directory {resolved_path}."


def remove_directory_tool(dir_path: str, recursive: bool = False) -> str:
    if not dir_path:
        return "Error: No `dir_path` provided."

    resolved_path = _resolve_dir_path(dir_path)
    if not resolved_path.exists():
        return f"Error: The directory {resolved_path} does not exist."
    if not resolved_path.is_dir():
        return f"Error: The path {resolved_path} is not a directory."

    try:
        if recursive:
            shutil.rmtree(resolved_path)
            return f"Removed directory tree {resolved_path}."
        resolved_path.rmdir()
        return f"Removed empty directory {resolved_path}."
    except OSError as error:
        if not recursive:
            return (
                f"Error: Remove directory failed due to\\n{error}\\n"
                "Hint: set recursive=true to remove non-empty directories."
            )
        return f"Error: Remove directory failed due to\\n{error}"
    except Exception as error:
        return f"Error: Remove directory failed due to\\n{error}"


def move_directory_tool(src_dir_path: str, dst_dir_path: str) -> str:
    if not src_dir_path:
        return "Error: No `src_dir_path` provided."
    if not dst_dir_path:
        return "Error: No `dst_dir_path` provided."

    src_path = _resolve_dir_path(src_dir_path)
    dst_path = _resolve_dir_path(dst_dir_path)

    if not src_path.exists():
        return f"Error: Source directory {src_path} does not exist."
    if not src_path.is_dir():
        return f"Error: Source path {src_path} is not a directory."

    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
    except Exception as error:
        return f"Error: Move directory failed due to\\n{error}"

    return f"Moved directory from {src_path} to {dst_path}."


def copy_directory_tool(
    src_dir_path: str,
    dst_dir_path: str,
    overwrite: bool = False,
) -> str:
    if not src_dir_path:
        return "Error: No `src_dir_path` provided."
    if not dst_dir_path:
        return "Error: No `dst_dir_path` provided."

    src_path = _resolve_dir_path(src_dir_path)
    dst_path = _resolve_dir_path(dst_dir_path)

    if not src_path.exists():
        return f"Error: Source directory {src_path} does not exist."
    if not src_path.is_dir():
        return f"Error: Source path {src_path} is not a directory."

    if dst_path.exists() and not overwrite:
        return (
            f"Error: Destination directory {dst_path} already exists. "
            "Set overwrite=true to allow copying into existing directory."
        )

    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_path, dst_path, dirs_exist_ok=overwrite)
    except Exception as error:
        return f"Error: Copy directory failed due to\\n{error}"

    return f"Copied directory from {src_path} to {dst_path}."


def directory_exists_tool(dir_path: str) -> str:
    if not dir_path:
        return "Error: No `dir_path` provided."

    resolved_path = _resolve_dir_path(dir_path)
    if not resolved_path.exists():
        return f"exists=False; is_directory=False; path={resolved_path}"

    return (
        f"exists=True; is_directory={resolved_path.is_dir()}; "
        f"path={resolved_path}"
    )


def get_working_directory_tool() -> str:
    return str(WORKING_DIR)


def dispatch_dir_io_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        if tool_name == "list_directory":
            return list_directory_tool(
                dir_path=str(arguments.get("dir_path", "")),
            )
        if tool_name == "create_directory":
            return create_directory_tool(
                dir_path=str(arguments.get("dir_path", "")),
            )
        if tool_name == "remove_directory":
            return remove_directory_tool(
                dir_path=str(arguments.get("dir_path", "")),
                recursive=bool(arguments.get("recursive", False)),
            )
        if tool_name == "move_directory":
            return move_directory_tool(
                src_dir_path=str(arguments.get("src_dir_path", "")),
                dst_dir_path=str(arguments.get("dst_dir_path", "")),
            )
        if tool_name == "copy_directory":
            return copy_directory_tool(
                src_dir_path=str(arguments.get("src_dir_path", "")),
                dst_dir_path=str(arguments.get("dst_dir_path", "")),
                overwrite=bool(arguments.get("overwrite", False)),
            )
        if tool_name == "directory_exists":
            return directory_exists_tool(
                dir_path=str(arguments.get("dir_path", "")),
            )
        if tool_name == "get_working_directory":
            return get_working_directory_tool()
    except Exception as error:
        return f"Error: Tool `{tool_name}` execution failed due to\\n{error}"

    return f"Error: Unknown tool `{tool_name}`."
