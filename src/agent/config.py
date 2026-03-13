import json
import os
from pathlib import Path
from typing import Any, Optional

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
