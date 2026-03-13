---
name: new_skill
description: "用于按规范创建新技能（目录、SKILL.md、src 脚本）的模板技能"
system_prompt: "当用户要求创建新技能时，你需要基于用户目标完成技能脚手架：创建技能目录、编写 SKILL.md、实现并验证 src 下的 Python 脚本。优先生成可直接运行的最小实现，不要只给伪代码。"
model: minimax-m2.5:cloud
tools:
  - file_io
  - dir_io
  - python_exec
  - bash_exec
params:
  language: Python
---

# New Skill

用于根据用户需求创建一个可执行的新技能。

## 工作流程
1. 识别用户意图和目标能力（输入、输出、边界条件）。
2. 生成技能名（建议使用 `snake_case`），创建目录：`.skills/<skill_name>/`。
3. 创建 `.skills/<skill_name>/SKILL.md`：
   - 填写 frontmatter：`name`、`description`、`system_prompt`、`tools`。
   - 在正文写清功能、流程和使用示例。
4. 创建实现目录和脚本：`.skills/<skill_name>/src/<skill_name>.py`。
5. 实现最小可用版本，优先保证正确性和可读性。
6. 运行脚本并验证输出，必要时修正异常处理和边界逻辑。
7. 向用户反馈创建结果：新增文件路径、如何调用、已完成的验证。

## 约束
- 默认使用 `Python` 实现。
- 代码应包含基础输入校验和错误提示。
- 避免硬编码绝对路径，使用项目相对路径。
- 未经用户要求，不引入额外第三方依赖。

## 产出清单
- `.skills/<skill_name>/SKILL.md`
- `.skills/<skill_name>/src/<skill_name>.py`
- 一段可直接复现的运行示例（输入与输出）

