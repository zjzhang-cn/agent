---
name: hello
description: "用于问候场景的应答技能"
system_prompt: "当用户问候时，你需要执行 hello 目录中的 hello.py 文件，并基于执行结果回复用户。"
model: minimax-m2.5:cloud
tools:
  - file_io
  - dir_io
  - python_exec
params:
  language: Python
---

# Hello Skill

## 工作流程
1. 识别问候输入（如：你好、hello、hi）。
2. 检查 `skills/hello/src/hello.py` 是否存在。
3. 执行脚本并读取输出。
4. 使用脚本输出生成自然语言回复。
