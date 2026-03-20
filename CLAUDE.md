# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI Agent built on OpenAI Chat Completions with multi-turn conversation support, tool calling, Skill loading, file upload, and streaming output. The agent supports tool groups for file I/O, directory I/O, Python execution, bash execution, and browser automation via Playwright. Requires Python >=3.12.

## Common Development Commands

- **Install dependencies** (using `uv`): `uv sync`
- **Install with pip**: `pip install -r requirements.txt && pip install -e .`
- **Run the agent interactively**: `uv run agent`
- **Run a single message**: `uv run agent "Hello"` or `uv run agent --user-message "Hello"`
- **List available skills**: `uv run agent --list-skills`
- **List available tool groups**: `uv run agent --list-tools`
- **Run browser automation test CLI**: `uv run browser-use-test`
- **Run tests** (if any): `uv run pytest`
- **Format code with black**: `uv run black src`
- **Lint with flake8**: `uv run flake8 src`
- **Check Python compilation**: `uv run python -m compileall src`

## Architecture

### Core Components

- `src/agent/cli.py` – Command-line interface parsing and interactive loop.
- `src/agent/ai_agent.py` – Main AIAgent class managing conversation, tool dispatch, and file uploads.
- `src/agent/config.py` – Environment variable loading and configuration parsing.
- `src/agent/skill.py` – Skill definition, loading, and merging.
- `src/agent/tool_utils.py` – Tool group definitions and resolution.
- `src/agent/streaming.py` – Streaming response consumption with tool call handling.

### Streaming

Responses are streamed by default. The `streaming` module extracts think content, parses tool arguments, and yields chunks for display while handling intermediate tool calls.

### Tool Groups

Tool groups are defined in `ai_agent.py` (`_TOOL_DISPATCHERS` and `_TOOL_NAME_TO_GROUP`). Each group has a dedicated module with dispatch functions:

- `file_io` – `read_file`, `write_file`, `edit_file`, `append_file`
- `dir_io` – `list_directory`, `create_directory`, `remove_directory`, `move_directory`, `copy_directory`, `directory_exists`
- `python_exec` – `run_python_script`, `run_python_code`
- `bash_exec` – `run_bash_command`, `run_shell_command`
- `browser_use` – `browser_use` (Playwright automation)

Tools are enabled via the `OPENAI_ENABLED_TOOLS` environment variable (comma-separated list or JSON array). Default order is defined in `DEFAULT_TOOL_GROUP_ORDER`.

### Skill System

Skills are YAML-frontmatter Markdown or TOML files placed in search directories (`skills/`, `.agent/skills/`, `~/.agent/skills/`). They can override the system prompt, model, enabled tools, and provide default parameters.

Skills are loaded via `--skill` flag or interactive `/skill load` command. Multiple skills can be merged; later skills take precedence.

### Configuration

Configuration is loaded from environment variables (`.env` file supported). Key variables:

- `OPENAI_API_KEY` – Required API key.
- `OPENAI_BASE_URL` – Optional base URL for OpenAI-compatible endpoints.
- `OPENAI_MODEL` – Default model (default: `gpt-4.1-mini`).
- `OPENAI_THINK` – Enables reasoning mode (passed as `think` parameter in API request).
- `OPENAI_ENABLED_TOOLS` – List of enabled tool groups.
- `OPENAI_INCLUDE_NATIVE_FILE_PARTS` – Whether to include native `file` message parts (default `true`).
- `OPENAI_SYSTEM_PROMPT` – Default system prompt template with `{placeholder}` support.
- `OPENAI_MAX_HISTORY_ROUNDS` – Number of conversation rounds to retain.
- `OPENAI_MAX_TOOL_CALL_ROUNDS` – Maximum tool call rounds per turn (default `8`).
- `SKILL_SEARCH_DIRS` – Colon-separated skill search paths.
- `COPAW_BROWSER_HEADLESS` / `BROWSER_HEADLESS` – Whether browser launches in headless mode (default `true`).
- `COPAW_BROWSER_USE_SYS_DEFAULT` / `BROWSER_USE_SYS_DEFAULT` – Whether to use system default browser (default `true`).
- `COPAW_BROWSER_BRING_TO_FRONT` – Whether to bring browser tab to front.
- `COPAW_BROWSER_AUTO_STOP` – Whether ordinary `stop` action can automatically close the browser.
- `COPAW_CHROMIUM_EXECUTABLE` – Path to Chromium/Chrome executable.

### File Uploads

Files can be uploaded via `--upload-file` or `/upload` command. Uploaded files are stored in the OpenAI Files API and their `file_id` is automatically referenced in subsequent messages. If the gateway does not support native `file` parts, set `OPENAI_INCLUDE_NATIVE_FILE_PARTS=false`.

### HTTP Client Customization

TLS verification and CA bundle can be configured via `OPENAI_SSL_VERIFY` and `OPENAI_CA_BUNDLE`. The HTTP client is built in `http_client.py` with support for proxy environment variables.

### Logging

Conversations, tool calls, and exceptions are logged to a file (`conversation.log` by default). Path configurable via `LOG_FILE_PATH`.

## Skill Development

- Skill templates are in `skills/skill-creator/SKILL_TEMPLATE.md`.
- A skill consists of a YAML frontmatter with fields: `name`, `description`, `system_prompt`, `model`, `tools`, `params`.
- The Markdown body after the frontmatter is appended to the system prompt.
- Skills can be directory‑based (`<skill_name>/SKILL.md`).
- Use `--skill` to load, `/skill load` in interactive mode.

## Browser Automation

The `browser-use-test` CLI provides a REPL for testing browser actions. It defaults to headed mode when run directly. The `browser_use` tool uses Playwright and supports actions like `start`, `open`, `click`, `snapshot`, `evaluate`, `stop`. Ensure Playwright browsers are installed via `playwright install`.

## Interactive Commands

While in the agent REPL, these commands are available:

- `/history` – View conversation history.
- `/reset` – Reset conversation.
- `/system <prompt>` – Update system prompt and reset.
- `/upload <path>` – Upload a file.
- `/files` – List uploaded files.
- `/fileparts [on|off]` – Toggle native file parts.
- `/skill list` – List skills.
- `/skill load <name>...` – Load skill(s).
- `/skill unload` – Unload current skill.
- `/skill reload` – Reload current skill.
- `help`, `?` – Show help.
- `quit`, `exit`, `退出` – End conversation.