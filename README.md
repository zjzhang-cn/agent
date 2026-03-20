# AI Agent

基于 OpenAI Chat Completions 的多轮对话 Agent，支持工具调用、Skill 合并加载、文件上传引用与流式输出。

## 功能特性

- 多轮对话：内置会话历史管理，可按轮数自动裁剪。
- 工具调用：支持文件、目录、Python、Shell（Windows 默认 PowerShell）与浏览器自动化工具。
- 文件上传：支持上传本地文件到 Files API，并在后续消息中附带 `file_id` 引用。
- Skill 机制：可按需加载单个或多个 Skill，自动合并提示词、参数与工具组。
- 流式输出：CLI 默认流式返回模型响应，支持中间工具调用回显。

## 环境要求

- Python `>=3.12`
- 推荐 `uv`（也支持 `pip`）

## 安装

```bash
git clone <your-repo-url>
cd agent
uv sync
```

使用 `pip`：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 配置

推荐在项目根目录创建 `.env`：

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
OPENAI_THINK=true
OPENAI_MAX_HISTORY_ROUNDS=20
OPENAI_MAX_TOOL_CALL_ROUNDS=8
OPENAI_ENABLED_TOOLS=file_io,dir_io,python_exec
OPENAI_INCLUDE_NATIVE_FILE_PARTS=true
OPENAI_SYSTEM_PROMPT=你是{role}，项目是{project}
OPENAI_SSL_VERIFY=true
OPENAI_CA_BUNDLE=/path/to/your/ca.pem
SKILL_SEARCH_DIRS=skills:.agent/skills:~/.agent/skills

COPAW_BROWSER_HEADED=false
COPAW_BROWSER_BRING_TO_FRONT=true
COPAW_BROWSER_AUTO_STOP=true
COPAW_CHROMIUM_EXECUTABLE=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

关键变量说明（按当前代码实现）：

- `OPENAI_API_KEY`：API Key，必填。
- `OPENAI_BASE_URL`：兼容 OpenAI 的接口地址，可选。
- `OPENAI_MODEL`：默认模型名，不配置时默认 `gpt-4.1-mini`。
- `OPENAI_THINK` / `THINK`：透传到请求体的 `think` 字段。
- `OPENAI_MAX_HISTORY_ROUNDS`：保留历史轮数，超出自动裁剪。
- `OPENAI_MAX_TOOL_CALL_ROUNDS`：单轮对话内的最大工具调用轮数，默认 `8`。
- `OPENAI_ENABLED_TOOLS` / `ENABLED_TOOLS`：默认启用工具组，支持逗号分隔或 JSON 数组。
- `OPENAI_INCLUDE_NATIVE_FILE_PARTS` / `INCLUDE_NATIVE_FILE_PARTS`：是否附带原生 `file` 消息片段，默认开启。
- `OPENAI_SYSTEM_PROMPT` / `SYSTEM_PROMPT`：默认系统提示词模板。
- `OPENAI_SSL_VERIFY` / `OPENAI_TLS_VERIFY`：是否校验 TLS 证书，默认 `true`。
- `OPENAI_CA_BUNDLE` / `SSL_CERT_FILE`：自定义 CA 证书路径（用于信任代理或网关自签名证书）。
- `SKILL_SEARCH_DIRS`：Skill 搜索目录（macOS/Linux 使用 `:` 分隔，Windows 使用 `;` 分隔）。
- `COPAW_BROWSER_HEADED` / `BROWSER_HEADED`：浏览器默认是否可见。
- `COPAW_BROWSER_BRING_TO_FRONT` / `BROWSER_BRING_TO_FRONT`：可见模式下是否前置标签页。
- `COPAW_BROWSER_AUTO_STOP` / `BROWSER_AUTO_STOP`：普通 `stop` 是否允许自动关闭浏览器。
- `COPAW_CHROMIUM_EXECUTABLE`：指定 Chromium/Chrome 可执行文件路径。

## 快速开始

交互模式：

```bash
uv run agent
```

兼容旧命令：

```bash
uv run ai-agent
```

单次消息模式（执行一次后退出）：

```bash
uv run agent 你好
uv run agent --user-message "你好，介绍一下你自己"
```

## CLI 参数

- `--system-prompt`：系统提示词模板，支持 `{placeholder}`。
- `--prompt-param key=value`：注入提示词变量，可重复传入。
- `--user-message`：直接提供首条用户消息。
- `--skill NAME`：加载指定 Skill，可重复。
- `--all-skills`：加载全部已发现 Skill。
- `--list-skills`：列出 Skill 后退出。
- `--list-tools`：列出工具组和工具后退出。
- `--upload-file PATH`：上传本地文件到 OpenAI（可重复传入多个）。
- `--native-file-parts`：强制开启原生 `file` 片段。
- `--no-native-file-parts`：强制关闭原生 `file` 片段。

示例：

```bash
uv run agent \
  --system-prompt "你是{role}，负责项目{project}，请始终使用中文回答。" \
  --prompt-param role=后端工程师 \
  --prompt-param project=agent

uv run agent --skill hello --user-message "你好"
uv run agent --list-tools
uv run agent --all-skills --user-message "帮我检查项目结构"
```

## 交互命令

- `/history`：查看会话历史。
- `/reset`：重置会话并恢复初始系统提示词。
- `/system <提示词>`：更新系统提示词并重置会话。
- `/upload <本地路径>`：上传文件到 OpenAI Files API。
- `/files`：查看已上传文件及其 `file_id`。
- `/fileparts`：查看原生 `file` 片段开关状态。
- `/fileparts on|off`：切换原生 `file` 片段开关。
- `/skill list` / `/skills`：列出可用 Skill。
- `/skill load <名称...>`：加载一个或多个 Skill。
- `/skill unload`：卸载当前 Skill 并恢复默认模型/工具。
- `/skill reload`：重载当前 Skill。
- `help` / `帮助` / `?`：显示帮助信息。
- `quit` / `exit` / `退出`：结束对话。

## browser-use-test（浏览器工具测试）

`browser-use-test` 默认会设置：

- `COPAW_BROWSER_HEADED=1`
- `COPAW_BROWSER_BRING_TO_FRONT=1`

即默认有头模式并尽量将页面切到前台，便于人工观察。

常见用法：

```bash
uv run browser-use-test --action start --headless
uv run browser-use-test --action open --page-id demo --url https://example.com
uv run browser-use-test --action snapshot --page-id demo
uv run browser-use-test --action click --page-id demo --selector "text=More information"
uv run browser-use-test --action stop --force-stop
```

REPL 模式：

```bash
uv run browser-use-test --interactive
```

示例输入：

```text
start headed=true
open page_id=demo url=https://example.com
snapshot page_id=demo
click page_id=demo selector="text=More information"
evaluate page_id=demo code='() => location.href'
stop force_stop=true
exit
```

## 文件上传与引用

1. 使用 `--upload-file` 或 `/upload` 上传本地文件。
2. 上传成功后会返回 `file_id`（例如 `file-abc123`）。
3. 后续用户消息会自动附带已上传文件列表，模型可引用对应 `file_id`。

若网关不支持消息中的原生 `file` 片段，可设置 `OPENAI_INCLUDE_NATIVE_FILE_PARTS=false`，或在命令行使用 `--no-native-file-parts`。

## Skill 机制

支持两种 Skill 文件格式：

- Markdown（YAML frontmatter + 正文）
- TOML（顶层字段）

默认搜索目录：

- `skills/`
- `.agent/skills/`
- `~/.agent/skills/`

支持目录式 Skill：`<skill_name>/SKILL.md`。

常用字段：

- `name`：Skill 名称
- `description`：Skill 描述
- `system_prompt`：系统提示词模板
- `model`：覆盖默认模型
- `tools`：启用工具组（`file_io`、`dir_io`、`python_exec`、`bash_exec`、`browser_use`）
- `params`：模板参数默认值

建议优先从 `skills/skill-creator/SKILL_TEMPLATE.md` 复制创建新 Skill。

## 内置工具组

- `file_io`：`read_file`、`write_file`、`edit_file`、`append_file`
- `dir_io`：`list_directory`、`create_directory`、`remove_directory`、`move_directory`、`copy_directory`
- `python_exec`：`run_python_script`、`run_python_code`
- `bash_exec`：`run_bash_command`、`run_shell_command`（支持 `shell=auto|bash|powershell|cmd`）
- `browser_use`：`browser_use(action=...)`

若需关闭默认工具，可设置 `OPENAI_ENABLED_TOOLS=[]`。

## 开发与测试

```bash
uv run agent --list-skills
uv run agent --list-tools
uv run python -m compileall src
```

## 常见问题

`OPENAI_API_KEY` 未设置：

- 启动时报错，请在 `.env` 或系统环境变量中配置。

上传文件返回 404：

- 常见于兼容网关未实现 `/files` 端点。可切换到官方 OpenAI 端点验证。

`--all-skills` 无可用 Skill：

- 确认 `skills/` 下存在有效 `SKILL.md`，并检查 `SKILL_SEARCH_DIRS`。