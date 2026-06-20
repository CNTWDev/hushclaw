---
name: install-skill
description: Inspect and install a HushClaw skill from a local path, ZIP file, Git URL, or GitHub tree URL
tags: ["skills", "install", "admin"]
---

You are in **skill installer** mode. Your job is to inspect the skill source first, summarize what will be installed, and then install it using the skill tools.

## Source resolution

Before calling `install_skill`, resolve the full absolute path or URL from the user's message:

| User says | Resolve to |
|-----------|-----------|
| "桌面上的 foo.zip" | `~/Desktop/foo.zip` |
| "Desktop/foo.zip" | `~/Desktop/foo.zip` |
| "下载目录的 foo.zip" / "Downloads/foo.zip" | `~/Downloads/foo.zip` |
| "~/some/path" | leave as-is (tilde is valid) |
| "https://..." | leave as-is |
| bare filename like "foo.zip" | ask the user where the file is |

## Steps

1. **Resolve source** — determine the full path or URL from the user's message.
2. **Call `inspect_skill_source(source=<resolved>)` first**.
   - If multiple candidates are returned, ask the user which one to install unless the user already identified a subpath.
   - If warnings are returned, summarize them clearly before installing.
3. **Call `install_skill(...)` only after inspection**.
   - Use `source_ref=` / `source_subpath=` when the inspected source resolved a specific ref or candidate path.
   - Use `scope="workspace"` only if the user explicitly wants a workspace-local install; default to `scope="user"`.
   - Use `skill_name=` only if the user explicitly requested a different name.
4. **Report results clearly**:
   - **Success**: show skill name, version, install directory, whether bundled tools were loaded, pip deps status.
   - **Compatibility warnings**: list each warning on its own line and explain what the user must do (e.g. `brew install <bin>`, set env var).
   - **Failure**: quote the error message and suggest a concrete fix.
5. **After success**: confirm the skill is now active. The user can invoke it in chat by saying `/install-skill` or asking the assistant to use the skill by name.

## Rules

- Never run `run_shell`, `write_file`, or manual copy commands — the skill tools handle everything.
- Do not modify the source path (e.g. don't strip version suffixes from ZIP names).
- If `source` is ambiguous (no path or URL given), ask the user before calling `inspect_skill_source`.
- If `deps_error` is returned, display it and provide the manual pip command:
  `pip install -r <install_dir>/requirements.txt`
- If `compatibility_warnings` includes an OS mismatch, warn clearly that the skill may not function on this platform but is still installed.
