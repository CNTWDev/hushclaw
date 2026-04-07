# HushClaw Skill 开发指南

> 本指南面向希望创建高质量 HushClaw Skill Package 的开发者，
> 覆盖从目录结构、SKILL.md 写法到 Python 工具开发的全流程。

---

## 1. Skill 的两种形态

| 形态 | 适用场景 | 结构 |
|------|---------|------|
| **纯 Prompt Skill** | 流程引导、角色扮演、SOP 固化 | 只有 `SKILL.md` |
| **带工具 Skill** | 需要调用外部库/命令/API | `SKILL.md` + `tools/*.py` + `requirements.txt` |

**判断依据**：如果 Agent 仅靠现有内置工具（`run_shell`、`read_file`、`browser_*` 等）就能完成任务，用纯 Prompt Skill 即可；
如果需要调用第三方库（`python-pptx`、`pdfplumber`、`psutil` 等），必须附带 Python 工具。

---

## 2. 目录结构

```
hushclaw-skill-{name}/          ← 仓库根目录，命名用 kebab-case
  SKILL.md                      ← 必填：LLM 系统提示
  tools/
    {name}_tools.py             ← 可选：@tool 装饰的 Python 工具
  requirements.txt              ← 可选：pip 依赖（不含 hushclaw 本身）
  README.md                     ← 建议：使用说明
```

**文件命名规则**：
- `tools/` 下的文件名用 `{slug}_tools.py` 格式，slug 与目录名保持一致（连字符换下划线）
- 多个工具文件按职责拆分，每文件 ≤ 300 行

---

## 3. SKILL.md front-matter（必填字段）

```yaml
---
name: my-skill              # kebab-case，全局唯一（SkillRegistry 按此查找）
description: 一句话描述      # 显示在 list_skills 输出里，影响 Agent 选择
tags: ["tag1", "tag2"]      # 分类标签，有助于搜索
author: Your Name
version: "1.0.0"            # Semantic Versioning
has_tools: true             # 有 tools/*.py 时写 true，否则 false 或省略
risk_level: low             # 可选：low | medium | high（默认 low）
input_contract: "需要一个有效的文件路径作为输入"  # 可选：一句话描述前置条件/输入要求
---
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | kebab-case，必须与目录名一致 |
| `description` | ✅ | 回答"它解决什么问题"，而非只是"它能做什么" |
| `tags` | 建议 | 影响搜索命中率 |
| `author` | 建议 | 维护者信息 |
| `version` | ✅ | 遵循语义版本号，破坏性改动升 major |
| `has_tools` | 建议 | 有 `tools/*.py` 时写 `true`，否则省略 |
| `risk_level` | 可选 | `low`（默认）/ `medium` / `high`。标记为 `high` 的 skill 在 Store 显示警示徽章，提醒用户谨慎安装 |
| `input_contract` | 可选 | 一句话描述 Skill 正常工作的前置条件或输入要求，帮助 Agent 判断何时调用 |

**quality checklist**：
- `name` 与目录名一致（避免混淆）
- `description` 要能回答"它解决什么问题"，而非只是"它能做什么"
- `version` 遵循语义版本号，破坏性改动时升 major
- 凡涉及写文件、执行命令、调用外部 API 的 Skill，务必设置 `risk_level: medium` 或 `high`
- `input_contract` 填写后，Agent 在调用前会主动验证是否满足条件

---

## 4. SKILL.md prompt 写法（高质量标准）

一个高质量的 SKILL.md body 由四个部分组成：

### 4.1 角色声明（1行）
```
你是 {领域} 专家，擅长 {核心能力}。
```

### 4.2 工具目录（有工具时必填）
```
可用工具：

- `tool_name(param1, param2)` — 一句话描述该工具做什么，强调输入/输出
- `tool_name2(path, max_chars)` — ...
```
**要求**：每个工具附上典型参数，让 Agent 无需查文档即知道怎么调用。

### 4.3 工作流程（核心部分）
```
工作流程：

1. 先用 `doc_info` 了解文件基本情况
2. 根据大小选择策略：
   - 小文件：一次全提取
   - 大文件：分批处理（每批 30 页）
3. 重要操作前告知用户，操作后确认结果
```
**要求**：
- 步骤要有**决策分支**（"如果…则…"），不能只列线性步骤
- 明确何时需要用户确认（破坏性、不可逆操作）
- 指出常见失败点和应对方式

### 4.4 边界与安全（可选但重要）
```
安全边界：
- 永远不执行 `rm -rf /`、`dd`、`mkfs`
- 删除操作前必须展示目标清单并等待确认
```
有危险操作的 Skill 必须写这部分。

---

## 5. Python 工具开发规范

### 5.1 最小骨架

```python
"""My skill tools — 一句话描述。

Dependencies:
  some-lib — pip install some-lib
"""
from __future__ import annotations
from pathlib import Path
from hushclaw.tools.base import ToolResult, tool


@tool(description="What this tool does, written for the LLM to read.")
def my_tool(param: str, optional_param: int = 10) -> ToolResult:
    """Short docstring (internal)."""
    # ... logic ...
    return ToolResult(output={"key": "value"})
```

### 5.2 ToolResult 使用规则

| 情况 | 写法 |
|------|------|
| 成功，返回结构化数据 | `ToolResult(output={"key": val})` |
| 成功，返回字符串 | `ToolResult.ok("message")` |
| 错误 | `ToolResult(error="message")` 或 `ToolResult.error("msg")` |

**不要** raise 异常给 LLM，始终用 `ToolResult(error=...)` 返回错误，Agent 会自动处理。

### 5.3 依赖检查（关键！）

每个用到可选第三方库的工具都要做懒导入 + 友好错误提示：

```python
@tool(description="...")
def my_tool(path: str) -> ToolResult:
    try:
        import some_lib  # type: ignore
    except ImportError:
        return ToolResult(error="some-lib is not installed. Run: pip install some-lib")
    # 继续正常逻辑
```

**为什么**：HushClaw 在安装 requirements.txt 之前会先注册工具，如果直接在模块顶层 import，会导致整个 skill 加载失败。

### 5.4 参数设计

- 参数名清晰，带类型注解（`str`、`int`、`list[str]`、`bool`）
- 可选参数提供合理默认值
- 以 `_` 开头的参数由框架注入（如 `_memory_store`、`_config`），对 LLM 隐藏
- 路径参数一律用 `Path(x).expanduser()` 处理 `~`

### 5.5 危险操作处理

需要用户确认的破坏性操作，接受框架注入的 `_confirm_fn` 参数：

```python
@tool(description="Delete a file. Asks for confirmation in REPL.")
def delete_file(path: str, _confirm_fn=None) -> ToolResult:
    if _confirm_fn and not _confirm_fn(f"Delete {path}?"):
        return ToolResult.ok("Cancelled.")
    Path(path).unlink()
    return ToolResult.ok(f"Deleted {path}")
```

### 5.6 requirements.txt 原则

- **只写实际 import 的包**，不写 `hushclaw`、`python`、`stdlib`
- 锁定大版本，不锁小版本：`pdfplumber>=0.10` 而非 `pdfplumber==0.11.4`
- 一行一个包，注释说明用途

```
pdfplumber>=0.10    # PDF text extraction
python-docx>=1.0   # Word document parsing
openpyxl>=3.1      # Excel reading/writing
```

---

## 6. 命名约定

| 对象 | 格式 | 例子 |
|------|------|------|
| 目录/skill name | `kebab-case` | `file-summary` |
| Python 文件 | `snake_case_tools.py` | `file_summary_tools.py` |
| 工具函数名 | `{slug}_{action}` | `doc_extract_text`, `pptx_create` |
| 工具参数 | `snake_case` | `start_page`, `max_chars` |

---

## 7. 常见反模式（避免）

| 反模式 | 正确做法 |
|--------|---------|
| SKILL.md 只有角色声明，没有工作流 | 加入分步流程和决策分支 |
| 工具函数直接 raise Exception | 返回 `ToolResult(error=...)` |
| 顶层 `import third_party`（可选依赖）| 懒导入 + 友好错误 |
| 工具 description 写给开发者看，而非 LLM | 写成 LLM 会理解的自然语言 |
| 一个工具做太多事 | 单一职责，功能拆分 |
| SKILL.md 工具列表没有参数示例 | 写出典型参数 `tool(path, max_chars)` |
| requirements.txt 里写了 `hushclaw` | 框架本身不需要列 |

---

## 8. 测试方法

```bash
# 1. 语法检查
python -m py_compile tools/my_tools.py

# 2. 安装并验证加载
pip install -e /path/to/hushclaw[server]
cp -r hushclaw-skill-mything ~/.hushclaw/skills/

# 3. 验证 skill 被识别
hushclaw repl
>>> list_skills

# 4. 触发 skill
>>> use_skill my-skill
>>> [执行 skill 描述的操作，观察工具调用]

# 5. 用 skill-builder 验证
>>> use_skill skill-builder
>>> skillbuild_validate /path/to/hushclaw-skill-mything
```

### 常见测试场景

- 正常路径：工具按预期返回结果
- 文件不存在：返回 `ToolResult(error=...)` 而非崩溃
- 依赖未安装：返回安装提示
- 超大输入：有 `max_chars` 等截断机制

---

## 9. 快速入门（用 skill-builder）

如果你不想手写，可以直接用 `skill-builder` skill 交互式生成骨架：

```
1. hushclaw repl
2. use_skill skill-builder
3. 按提示回答：技能名、描述、操作步骤、是否需要工具
4. 审阅生成的 SKILL.md + tools.py 草稿
5. 保存到 skill-packages/ 目录
6. 修改工具实现（TODOs 部分）
7. skillbuild_validate 验证
```

---

## 10. 发布到社区（可选）

```
git init hushclaw-skill-{name}
cd hushclaw-skill-{name}
git add .
git commit -m "feat: initial skill release v1.0.0"
git remote add origin https://github.com/{you}/hushclaw-skill-{name}
git push -u origin main
```

其他用户即可通过 HushClaw Skill Store 安装，或直接 clone 到 `skill_dir`。

---

## 11. 参考实现（按复杂度排序）

| Skill | 类型 | 学什么 |
|-------|------|--------|
| `hushclaw-skill-run-shell` | 纯 Prompt | 安全边界写法、场景分类 |
| `hushclaw-skill-file-summary` | 带工具 | 懒导入模式、大文件分批处理策略 |
| `hushclaw-skill-pptx` | 带工具 | 工具目录写法、工作流细化 |
| `hushclaw-skill-auto-monitor` | 带工具 | 告警阈值配置、多工具协作 |
| `hushclaw-skill-builder` | 带工具 | 元 skill（生成 skill 的 skill） |

---

## 12. 工具健壮性规范（防踩坑四条）

> 这四条规范来自真实生产中反复出现的错误，每次新建或修改工具时必须对照检查。

---

### 12.1 标准参数名约定

框架的 `ToolExecutor._normalize_kwargs()` 会自动把 LLM 常用别名映射到标准参数名。
**写工具函数时，首选以下标准名**，避免 "missing required argument" 类错误：

| 语义 | 标准参数名 | 常见别名（框架自动映射） |
|------|-----------|------------------------|
| 搜索词 / 关键词 | `query` | `keyword`, `search`, `q`, `term` |
| 文件路径 | `path` | `file`, `filepath`, `file_path` |
| 网页/资源地址 | `url` | `link`, `href`, `uri` |
| 分页数量 | `limit` | `count`, `n`, `num`, `max_results`, `size` |
| 待办/任务标题 | `title` | `name`, `task`, `text` |

> 如果业务语义上必须用其他名字（如 TikTok Research API 的 `field_name: "keyword"`），
> 区分 **Python 参数名（给 LLM 看）** 与 **API 请求参数名（发给外部服务）**，见 12.3。

---

### 12.2 必填参数前置校验

每个必填参数在函数入口就检查，不要等到内部逻辑报错。

```python
@tool(description="Search videos. query (required): keyword or topic.")
def my_search(query: str, limit: int = 10) -> ToolResult:
    # ✅ 入口校验：空字符串立即返回友好错误
    if not query.strip():
        return ToolResult.error("query cannot be empty — provide a search keyword or topic")

    # 其他参数的范围保护
    limit = max(1, min(limit, 100))
    # ... 正常逻辑
```

**规则**：
- 必填 `str` 参数：检查 `not param.strip()`
- 必填 `int/float`：检查合理范围，并用 `max/min` 截断
- 错误消息要说明"应该传什么"，不要只说"参数为空"

---

### 12.3 Python 参数名与 API 请求参数名分离

当外部 API 的字段名与推荐的 Python 参数名不一致时，**在函数内部显式映射**，并加注释说明。

```python
@tool(description="Search TikTok videos. query (required): keyword/topic.")
def tiktok_search_videos(query: str) -> ToolResult:
    # Python 参数名是 `query`（LLM 友好），API 字段名是 `keyword`（TikTok Research API 术语）
    body = {
        "query": {
            "and": [{"operation": "IN", "field_name": "keyword", "field_values": [query]}]
        }
    }
    # ^ 这里 "keyword" 是 TikTok API 的 field_name 枚举值，不是我们的参数名
```

**反模式**（会导致 400 Bad Request）：

```python
# ❌ 错误：Python 参数名 keyword，API 请求也用 keyword，但 ScrapeCreators 实际期望 query
def tiktok_search_videos(keyword: str):
    params = {"keyword": keyword}  # API 400
```

---

### 12.4 API Key 统一使用环境变量

**绝对不要硬编码 API Key**，即使是"测试用"的临时 key。

```python
# ✅ 正确：环境变量 + 友好安装提示
_INSTALL_HINT = (
    "MY_API_KEY not set.\n"
    "Get a free key at https://example.com and run:\n"
    "  export MY_API_KEY='your_key'"
)

def _get_api_key() -> tuple[str, str | None]:
    """Return (api_key, error_message). error_message is set when key is missing."""
    key = os.environ.get("MY_API_KEY", "").strip()
    if not key:
        return "", _INSTALL_HINT
    return key, None

@tool(description="...")
def my_tool(query: str) -> ToolResult:
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    # 正常使用 api_key
```

```python
# ❌ 错误：硬编码，泄露风险 + 过期后全部工具崩溃
def _get_api_key():
    return os.environ.get("MY_API_KEY", "hardcoded_default_key")
```

**额外建议**：在 `requirements.txt` 旁边维护一个 `env.example` 文件，列出所需的环境变量和说明。

---

### 12.5 规范自查清单

写完或修改工具后，用以下清单过一遍：

- [ ] 必填参数是否使用了标准参数名（`query` / `path` / `url` / `limit` / `title`）？
- [ ] 必填 `str` 参数在函数入口是否有 `if not x.strip(): return ToolResult.error(...)` 检查？
- [ ] Python 参数名与外部 API 请求字段名是否在代码中有清晰注释区分？
- [ ] 所有 API Key 是否通过 `os.environ.get()` 读取，且缺失时返回含安装提示的错误？
- [ ] `@tool(description=...)` 中是否明确标注了哪些参数是 **required**（必填）？
