# AI Agent

基于 OpenAI API 的多轮对话 Agent，支持工具调用和可插拔 Skill。

## 功能特性

- 多轮对话：内置会话历史管理和轮数裁剪。
- 工具调用：支持文件、目录、Python 执行、Bash 执行。
- Skill 机制：可按需加载一个或多个 Skill，并支持自动合并。
- 流式输出：CLI 对话默认流式返回。

## 环境要求

- Python `>=3.12`
- 建议使用 `uv`（也可用 `pip`）

## 安装

```bash
git clone <your-repo-url>
cd agent
uv sync
```

如果你使用 `pip`：

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
OPENAI_ENABLED_TOOLS=file_io,dir_io,python_exec
OPENAI_SYSTEM_PROMPT=你是{role}，项目是{project}
SKILL_SEARCH_DIRS=skills:.agent/skills:~/.agent/skills
COPAW_BROWSER_HEADED=false
COPAW_BROWSER_BRING_TO_FRONT=true
COPAW_BROWSER_AUTO_STOP=true
```

也支持短变量名：

```env
KEY=your-api-key
BASE_URL=https://api.openai.com/v1
MODEL=gpt-4.1-mini
THINK=true
```

关键变量说明：

- `OPENAI_API_KEY` / `KEY`：API Key。
- `OPENAI_BASE_URL` / `BASE_URL`：兼容 OpenAI 的接口地址。
- `OPENAI_MODEL` / `MODEL`：默认模型名。
- `OPENAI_THINK` / `THINK`：透传到请求体的 `think` 字段。
- `OPENAI_MAX_HISTORY_ROUNDS`：保留的历史轮数，超出后自动裁剪。
- `OPENAI_ENABLED_TOOLS` / `ENABLED_TOOLS`：默认启用的工具组，支持逗号分隔字符串或 JSON 数组，例如 `file_io,dir_io` 或 `["file_io", "browser_use"]`。
- `OPENAI_SYSTEM_PROMPT` / `SYSTEM_PROMPT`：默认系统提示词模板。
- `SKILL_SEARCH_DIRS`：Skill 搜索目录，使用系统路径分隔符连接（macOS/Linux 为 `:`，Windows 为 `;`）。
- `COPAW_BROWSER_HEADED`：`browser_use` 的默认浏览器可见性。`true` 表示默认可见窗口（headed），`false` 表示默认无界面（headless）。`action=start` 里显式传 `headed` 时会覆盖该默认值。
- `COPAW_BROWSER_BRING_TO_FRONT`：是否在可见模式下自动调用 `page.bring_to_front()` 将标签页切到前台。默认 `true`，设为 `false` 可关闭。
- `COPAW_BROWSER_AUTO_STOP`：是否允许普通 `action=stop` 自动关闭浏览器。默认 `true`；设为 `false` 后，普通 stop 会被忽略（可通过 `force_stop=true` 强制关闭）。

## 快速开始

交互模式：

```bash
uv run agent
```

兼容旧命令：

```bash
uv run ai-agent
```

单次消息（执行一次后退出）：

```bash
uv run agent 你好
uv run agent --user-message "你好，介绍一下你自己"
```

## CLI 参数

- `--system-prompt`：系统提示词模板，支持 `{placeholder}`。
- `--prompt-param key=value`：注入模板变量，可重复传入。
- `--user-message`：直接提供首条用户消息。
- `--skill NAME`：加载指定 Skill，可重复。
- `--all-skills`：加载全部发现的 Skill。
- `--list-skills`：列出 Skill 后退出。
- `--list-tools`：列出所有工具组及其工具后退出。

示例：

```bash
uv run agent \
  --system-prompt "你是{role}，负责项目{project}，请始终使用中文回答。" \
  --prompt-param role=后端工程师 \
  --prompt-param project=agent
```

```bash
uv run agent --skill hello --user-message "你好"
uv run agent --list-tools
uv run agent --all-skills --user-message "帮我检查项目结构"
uv run agent --all-skills --user-message "创建一个求最大质数的SKILL，用Python实现, 描述上加上不要要验证的"
uv run agent --all-skills --user-message "更新find_max_prime技能， 描述上加上不要要验证的说明"
```

## 交互命令

- `/history`：查看当前会话历史。
- `/reset`：重置会话并恢复初始系统提示词。
- `/system <提示词>`：更新系统提示词并重置会话。
- `quit` / `exit` / `退出`：结束对话。

## 结束对话

AI 可以在任务完成后主动结束对话。系统提示词中包含结束指引，AI 会根据指引使用以下关键词明确告知用户对话即将结束：

- `<<再见>>`
- `<<结束>>`
- `<<完成>>`
- `<<结束对话>>`
- `<<MESSAGE_END>>`
- `<<END>>`

当 AI 输出包含上述关键词时，对话将自动结束。

## Skill 机制

支持两种 Skill 文件格式：

- Markdown：YAML frontmatter + Markdown 正文
- TOML：顶层字段定义

默认搜索目录：

- `skills/`
- `.agent/skills/`
- `~/.agent/skills/`

也支持目录式 Skill：`<skill_name>/SKILL.md`。

Skill 常用字段：

- `name`：Skill 名称
- `description`：简要说明
- `system_prompt`：系统提示词模板
- `model`：覆盖默认模型
- `tools`：启用工具组（`file_io`、`dir_io`、`python_exec`、`bash_exec`、`browser_use`）
- `params`：默认模板参数

推荐优先从 `skills/skill-creator/SKILL_TEMPLATE.md` 复制创建新的 `SKILL.md`。该模板已经包含统一的结构：frontmatter、工作流程、约束、输出要求，以及“提示词要与实际工具能力一致”的默认约束。

## 内置工具组

- `file_io`：`read_file`、`write_file`、`edit_file`、`append_file`
- `dir_io`：目录列表/创建/删除/移动/复制/存在性检查
- `python_exec`：`run_python_script`、`run_python_code`
- `bash_exec`：`run_bash_command`
- `browser_use`：`browser_use(action=...)`，支持 Playwright 网页打开、交互、快照、截图等操作

如果需要禁用全部默认工具，可以将 `OPENAI_ENABLED_TOOLS` 设为 `[]`。当 Skill 显式声明 `tools` 时，仍会优先使用 Skill 的工具组配置；未声明时会回退到这里设置的默认值。

## 常见问题

`OPENAI_API_KEY` 未设置：

- 启动时会报错，请在 `.env` 或系统环境变量中配置。

`--all-skills` 无可用 Skill：

- 确认 `skills/` 下存在有效 Skill 文件，或检查 `SKILL_SEARCH_DIRS` 是否正确。