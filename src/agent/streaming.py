from typing import Any, Dict, List, Optional, Tuple


def normalize_reasoning_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
            elif isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
            else:
                text = str(item).strip()
                if text:
                    parts.append(text)
        return "\\n".join(parts)

    if isinstance(value, dict):
        for key in ["text", "content", "reasoning_content", "reasoning"]:
            if key in value:
                return normalize_reasoning_value(value[key])
        return str(value).strip()

    return str(value).strip()


def extract_think_content(message: Any) -> Optional[str]:
    candidates = [
        getattr(message, "reasoning_content", None),
        getattr(message, "reasoning", None),
    ]

    for candidate in candidates:
        normalized = normalize_reasoning_value(candidate)
        if normalized:
            return normalized

    return None


def normalize_stream_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None) or getattr(item, "content", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    if isinstance(value, dict):
        for key in ["text", "content", "reasoning_content", "reasoning"]:
            if key in value:
                return normalize_stream_text(value[key])
        return ""

    for key in ["text", "content", "reasoning_content", "reasoning"]:
        nested_value = getattr(value, key, None)
        if nested_value is not None:
            return normalize_stream_text(nested_value)

    return ""


def extract_delta_fields(delta: Any) -> Tuple[str, str]:
    reasoning_value = getattr(delta, "reasoning_content", None)
    if reasoning_value is None:
        reasoning_value = getattr(delta, "reasoning", None)

    content_value = getattr(delta, "content", None)
    reasoning_text = normalize_stream_text(reasoning_value)
    content_text = normalize_stream_text(content_value)

    return reasoning_text, content_text


def parse_tool_arguments(arguments: str) -> Dict[str, Any]:
    if not arguments:
        return {}

    try:
        import json

        parsed = json.loads(arguments)
    except Exception:
        return {"__error__": f"Invalid JSON arguments: {arguments}"}

    if not isinstance(parsed, dict):
        return {"__error__": "Tool arguments must be a JSON object."}

    return parsed


def consume_stream_with_tool_calls(stream: Any, emit_output: bool = True) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    think_parts: List[str] = []
    assistant_parts: List[str] = []
    tool_calls_by_index: Dict[int, Dict[str, Any]] = {}

    think_started = False
    answer_started = False
    fallback_index = 0

    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue

        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue

        reasoning_text, content_text = extract_delta_fields(delta)
        if reasoning_text:
            think_parts.append(reasoning_text)
            if emit_output:
                if not think_started:
                    print("THINK: ", end="", flush=True)
                    think_started = True
                print(reasoning_text, end="", flush=True)

        if content_text:
            assistant_parts.append(content_text)
            if emit_output:
                if think_started and not answer_started:
                    print()
                if not answer_started:
                    print("AI: ", end="", flush=True)
                    answer_started = True
                print(content_text, end="", flush=True)

        delta_tool_calls = getattr(delta, "tool_calls", None) or []
        for tool_delta in delta_tool_calls:
            raw_index = getattr(tool_delta, "index", None)
            if isinstance(raw_index, int):
                index = raw_index
            else:
                index = fallback_index
                fallback_index += 1

            state = tool_calls_by_index.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                },
            )

            tool_call_id = getattr(tool_delta, "id", None)
            if tool_call_id:
                state["id"] = tool_call_id

            tool_type = getattr(tool_delta, "type", None)
            if tool_type:
                state["type"] = tool_type

            function = getattr(tool_delta, "function", None)
            if function is None:
                continue

            function_name = getattr(function, "name", None)
            if function_name:
                state["function"]["name"] += function_name

            function_arguments = getattr(function, "arguments", None)
            if function_arguments:
                state["function"]["arguments"] += function_arguments

    if emit_output and think_started:
        print()
    if emit_output and answer_started:
        print("\n")

    think_content = "".join(think_parts).strip() or None
    assistant_reply = "".join(assistant_parts)
    ordered_tool_calls = [tool_calls_by_index[index] for index in sorted(tool_calls_by_index)]

    return assistant_reply, think_content, ordered_tool_calls
