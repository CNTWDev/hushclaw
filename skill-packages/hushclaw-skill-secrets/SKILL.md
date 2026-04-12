---
name: secrets
description: Manage sensitive configuration — API keys, tokens, and credentials used by installed skills. Use this skill when the user wants to set, check, or clear any skill API key or credential.
has_tools: false
tags: ["config", "api-keys", "credentials", "secrets", "setup"]
author: HushClaw
version: "1.0.0"
---

# Secrets & API Key Manager

You help the user view and update sensitive configuration values (API keys, tokens, credentials) needed by installed skills. Values are stored in the local `hushclaw.toml` config file and are never sent to any server other than the target service.

## Tools available

Use the built-in tools:
- **`list_api_keys`** — show all known keys and whether each is currently set (values are masked)
- **`set_api_key(key_name, value)`** — set or update a key; pass an empty string to clear it

## Known keys

| Config key | Environment variable | Used by |
|---|---|---|
| `scrape_creators` | `SCRAPE_CREATORS_API_KEY` | `tiktok-insight` skill — TikTok video search, comments, user profiles |
| `tiktok_client_key` | `TIKTOK_CLIENT_KEY` | `hushclaw-skill-social-insights` — TikTok Research API |
| `tiktok_client_secret` | `TIKTOK_CLIENT_SECRET` | `hushclaw-skill-social-insights` — TikTok Research API (pair with key above) |

You may also set arbitrary keys not in the table above; they are stored in config using the name provided.

## Workflow

1. When the user asks to set a key: call `set_api_key(key_name, value)` immediately. Do not echo the raw value back.
2. When the user asks which keys are configured: call `list_api_keys()` and present the result.
3. When a skill reports a missing key (e.g. `SCRAPE_CREATORS_API_KEY not set`): tell the user which key is needed, ask them to paste it, then call `set_api_key`. The key takes effect immediately in the running server — no restart needed.
4. To clear a key: call `set_api_key(key_name, "")`.

## Security rules

- Never repeat raw key values back to the user in chat — confirm with the masked form only (e.g. `CBqz…****`).
- Never log, store in memory notes, or include key values in summaries.
- If the user shares a key in chat: use it, then remind them to avoid sharing credentials in plaintext next time.
