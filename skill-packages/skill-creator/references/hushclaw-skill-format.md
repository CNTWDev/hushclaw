# HushClaw Skill 格式规范

## 目录结构

```
skill-packages/{skill-name}/        ← kebab-case 命名
  SKILL.md                          ← 必填
  tools/
    {slug}_tools.py                 ← 可选：@tool 装饰的 Python 工具
  requirements.txt                  ← 可选：pip 依赖
  references/                       ← 可选：供 include_files 引用的参考文档
  assets/                           ← 可选：模板文件、静态资源
  scripts/                          ← 可选：可执行脚本
```

**判断是否需要 tools/**: 如果 Agent 用内置工具（`run_shell`、`read_file`、`fetch_url`、`browser_*` 等）就能完成任务，用纯 Prompt Skill。只有需要调用第三方 Python 库时才写 tools。

---

## SKILL.md front-matter

```yaml
---
name: my-skill              # kebab-case，全局唯一，与目录名一致
description: ...            # 触发描述，影响 Agent 是否选择该 skill（见下方说明）
tags: ["tag1", "tag2"]      # 分类标签，辅助搜索
author: Your Name
version: "1.0.0"            # Semantic Versioning
has_tools: true             # 有 tools/*.py 时写 true
include_files: ["references/doc.md", "assets/template.html"]  # 可选：体内联入的文件
requires:                   # 可选：声明外部依赖
  bins: [git, ffmpeg]       # 需要在 PATH 中存在的命令
  env: [GITHUB_TOKEN]       # 需要设置的环境变量
---
```

### description 写法要点

description 是触发机制。要"稍微激进"——包含典型使用场景和同义词。

**弱**（容易被跳过）：
> "生成 Excel 报表"

**好**（有多个触发点）：
> "将数据生成标准化 Excel 报表。用户要求生成报表、导出数据、整理成表格、或提到 Excel/CSV 文件时触发，即使没有明确说'生成报表'。"

---

## SKILL.md body（prompt 内容）

```markdown
# 技能名称

你是 XX 专家，擅长 YY。（一行角色声明）

## 可用工具
- `tool_name(param1, param2)` — 做什么（有自定义工具时填写）

## 工作流程
1. 第一步
2. 如果 A 则 X，如果 B 则 Y（要有决策分支）
3. 危险操作前告知用户

## 安全边界（有危险操作时填写）
- 永远不执行 XX
- YY 操作前必须展示目标清单并等待确认
```

---

## {baseDir} 和 include_files

**`{baseDir}`**：在 body 里可用此占位符引用 skill 目录的绝对路径。

```markdown
运行分析脚本：
python {baseDir}/scripts/analyze.py --input <file>
```

**`include_files`**：列表中的文件在加载时自动追加到 body 末尾（作为附录），适合放大型参考文档。

```yaml
include_files: ["references/api-reference.md", "assets/template.html"]
```

路径相对于 skill 目录。不支持路径遍历（`../` 被拒绝）。

---

## Python 工具开发

### 最小骨架

```python
from __future__ import annotations
from hushclaw.tools.base import ToolResult, tool

@tool(description="做什么——写给 LLM 读，用自然语言描述输入/输出。")
def my_tool(param: str, optional_param: int = 10) -> ToolResult:
    # ... 逻辑 ...
    return ToolResult(output={"key": "value"})
```

### ToolResult 用法

| 情况 | 写法 |
|------|------|
| 成功，返回结构化数据 | `ToolResult(output={"key": val})` |
| 成功，返回字符串 | `ToolResult.ok("message")` |
| 错误 | `ToolResult.error("message")` |

**不要** raise 异常给 LLM，始终用 `ToolResult.error(...)` 返回错误。

### 可选依赖：懒导入模式

```python
@tool(description="...")
def my_tool(path: str) -> ToolResult:
    try:
        import some_lib
    except ImportError:
        return ToolResult.error("some-lib 未安装。运行：pip install some-lib")
    # 继续正常逻辑
```

**为什么**：HushClaw 在安装 requirements.txt 之前就会注册工具，顶层 import 可选依赖会导致 skill 加载失败。

### 危险操作处理

```python
@tool(description="删除文件，在 REPL 中会要求用户确认。")
def delete_file(path: str, _confirm_fn=None) -> ToolResult:
    if _confirm_fn and not _confirm_fn(f"删除 {path}？"):
        return ToolResult.ok("已取消。")
    Path(path).unlink()
    return ToolResult.ok(f"已删除 {path}")
```

`_confirm_fn` 以 `_` 开头，由框架注入，对 LLM 隐藏。

### requirements.txt 原则

- 只写实际 import 的包，不写 `hushclaw`、`python`、stdlib
- 锁定大版本：`pdfplumber>=0.10` 而非 `pdfplumber==0.11.4`

---

## 命名约定

| 对象 | 格式 | 示例 |
|------|------|------|
| skill 目录/name | `kebab-case` | `file-summary` |
| tools 文件 | `{slug}_tools.py` | `file_summary_tools.py` |
| 工具函数名 | `{slug}_{action}` | `doc_extract_text` |
| 工具参数 | `snake_case` | `start_page`, `max_chars` |

---

## 常见反模式

| 反模式 | 正确做法 |
|--------|---------|
| SKILL.md 只有角色声明，没有工作流 | 加分步流程和决策分支 |
| 工具函数直接 raise Exception | 返回 `ToolResult.error(...)` |
| 顶层 `import third_party`（可选依赖） | 懒导入 + 友好错误提示 |
| 工具 description 写给开发者，非 LLM | 写 LLM 能理解的自然语言 |
| 一个工具做太多事 | 单一职责，功能拆分 |

---

## 安装与测试

```bash
# 1. 语法检查
python -m py_compile tools/my_tools.py

# 2. 复制到 skill 目录
cp -r skill-packages/my-skill ~/.hushclaw/skills/

# 3. 验证被识别
hushclaw repl
>>> list_skills

# 4. 触发并测试
>>> use_skill my-skill
```

或者在 `hushclaw.toml` 中配置：
```toml
[tools]
skill_dir = "~/.hushclaw/skills"
```
