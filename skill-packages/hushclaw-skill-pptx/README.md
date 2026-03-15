# hushclaw-skill-pptx

A bundled HushClaw skill package for creating and editing PowerPoint presentations using [python-pptx](https://python-pptx.readthedocs.io/).

## Install via HushClaw Web UI

1. Open HushClaw Web UI → **Skills** tab → **Store**
2. Find **pptx-editor** and click **Install**
3. HushClaw will automatically:
   - Clone this repo into your `skill_dir`
   - Run `pip install -r requirements.txt` in the HushClaw Python environment
   - Load the tools from `tools/pptx_tools.py`
4. Start chatting: *"帮我新建一个 test.pptx，加一页标题页"*

No restart required.

## Manual Install

```bash
# 1. Clone into your skill directory (default: ~/.hushclaw/skills/)
git clone https://github.com/CNTWDev/hushclaw-skill-pptx ~/.hushclaw/skills/hushclaw-skill-pptx

# 2. Install dependencies into the same Python environment as HushClaw
pip install -r ~/.hushclaw/skills/hushclaw-skill-pptx/requirements.txt

# 3. Restart hushclaw serve — tools are auto-loaded on startup
hushclaw serve
```

## Available Tools

| Tool | Description |
|------|-------------|
| `pptx_create(path)` | Create a new blank PPTX file |
| `pptx_info(path)` | Get slide count and title list |
| `pptx_read_slide(path, slide_index)` | Read all text from a specific slide |
| `pptx_extract_all_text(path)` | Extract text from all slides |
| `pptx_add_title_slide(path, title, subtitle)` | Add a title slide |
| `pptx_add_text_slide(path, title, content)` | Add a text content slide |
| `pptx_set_slide_text(path, slide_index, placeholder_index, text)` | Modify placeholder text |
| `pptx_delete_slide(path, slide_index)` | Delete a slide (0-based index) |

## Building Your Own Skill Package

This repo is a reference implementation of the **HushClaw Bundled Skill Package** format:

```
your-skill-package/
  SKILL.md              ← LLM system prompt (required)
  tools/
    your_tools.py       ← @tool-decorated Python functions (optional)
  requirements.txt      ← pip dependencies for your tools (optional)
  README.md
```

### SKILL.md format

```markdown
---
name: my-skill
description: One-line description shown in the Skill Store
tags: ["tag1", "tag2"]
author: Your Name
version: "1.0.0"
---

System prompt text here…
```

### tools/*.py format

```python
from hushclaw.tools.base import ToolResult, tool

@tool(description="What this tool does.")
def my_tool(param: str) -> ToolResult:
    return ToolResult(output={"result": param})
```

Tools are loaded at install time and on every server startup (as long as the skill repo is in `skill_dir`). No restart required after install.

### requirements.txt

Standard pip requirements file. HushClaw installs these into the **same Python environment** that runs `hushclaw serve`, so your tools can import them immediately.
