"""Setup commands: init (interactive wizard) and doctor (diagnostics)."""
from __future__ import annotations

import asyncio
import sys


def _provider_env_var(provider_name: str) -> str:
    """Return the canonical env-var name for the given provider."""
    n = provider_name.lower()
    if "gemini" in n or "google" in n:
        return "GEMINI_API_KEY"
    if "minimax" in n:
        return "MINIMAX_API_KEY"
    if "openai" in n:
        return "OPENAI_API_KEY"
    if "aigocode" in n:
        return "AIGOCODE_API_KEY"
    return "ANTHROPIC_API_KEY"


def cmd_init(args) -> int:
    from hushclaw.config.loader import get_config_dir
    from hushclaw.config.writer import write_config_toml

    cfg_dir = get_config_dir()
    cfg_path = cfg_dir / "hushclaw.toml"

    _hr = "─" * 42

    print(f"\nHushClaw Setup")
    print(_hr)

    if cfg_path.exists():
        try:
            ans = input(
                f"\nConfig already exists at:\n  {cfg_path}\n\nOverwrite? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if ans != "y":
            print("Aborted — existing config unchanged.")
            return 0

    # Step 1: Provider
    print("\nStep 1/4: Provider\n")
    print("  1. Anthropic (Claude)  [recommended]")
    print("  2. Ollama (local, no API key needed)")
    print("  3. OpenAI-compatible")
    print("  4. AIGOCODE relay")
    try:
        raw = input("\nProvider [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    provider_map = {
        "1": "anthropic-raw",
        "2": "ollama",
        "3": "openai-raw",
        "4": "aigocode-raw",
        "": "anthropic-raw",
    }
    provider_name = provider_map.get(raw)
    if provider_name is None:
        print(f"[Error] Invalid choice: {raw!r}")
        return 1

    # Step 2: API key
    api_key = ""
    if provider_name in ("anthropic-raw", "openai-raw", "aigocode-raw"):
        import getpass
        provider_label = (
            "Anthropic" if "anthropic" in provider_name else
            "AIGOCODE" if "aigocode" in provider_name else
            "OpenAI"
        )
        print(f"\nStep 2/4: API Key\n")
        print(f"Enter your {provider_label} API key (input is hidden):")
        try:
            api_key = getpass.getpass("API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if not api_key:
            env_var = _provider_env_var(provider_name)
            print(f"[Warning] No API key entered — you can set it later via {env_var}")
    else:
        print("\nStep 2/4: API Key\n")
        print("  (Ollama runs locally — no API key required)")

    # Step 3: Primary model
    print("\nStep 3/4: Primary model (for conversations)\n")
    model_choices = {
        "anthropic-raw": [
            ("claude-sonnet-4-6", "recommended"),
            ("claude-haiku-4-5-20251001", "faster, cheaper"),
            ("claude-opus-4-6", "most capable"),
        ],
        "ollama": [
            ("llama3.2", "recommended for local use"),
            ("mistral", "alternative"),
            ("phi3", "lightweight"),
        ],
        "openai-raw": [
            ("gpt-4o", "recommended"),
            ("gpt-4o-mini", "faster, cheaper"),
            ("gpt-4-turbo", "previous generation"),
        ],
        "aigocode-raw": [
            ("gpt-4o-mini", "recommended"),
            ("gpt-4o", "higher quality"),
            ("gpt-4.1-mini", "balanced"),
        ],
    }
    choices = model_choices.get(provider_name, [("claude-sonnet-4-6", "default")])
    for i, (m, note) in enumerate(choices, 1):
        rec = "  [recommended]" if i == 1 else ""
        print(f"  {i}. {m}  ({note}){rec}")
    print(f"  4. Custom...")

    try:
        raw_model = input("\nModel [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    if raw_model == "" or raw_model == "1":
        model = choices[0][0]
    elif raw_model == "2" and len(choices) >= 2:
        model = choices[1][0]
    elif raw_model == "3" and len(choices) >= 3:
        model = choices[2][0]
    elif raw_model == "4":
        try:
            model = input("Custom model name: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if not model:
            model = choices[0][0]
    else:
        model = raw_model if not raw_model.isdigit() else choices[0][0]

    # Step 4: Auxiliary model (cheap_model — same provider as primary)
    print("\nStep 4/4: Auxiliary model (for profile learning, summaries, belief updates)\n")
    aux_choices = {
        "anthropic-raw": [
            ("claude-haiku-4-5-20251001", "fast, ~5x cheaper than Sonnet"),
            ("claude-sonnet-4-6", "same as primary"),
            ("claude-opus-4-6", "most capable"),
        ],
        "ollama": [
            ("", "same as primary (Ollama has no per-token cost)"),
        ],
        "openai-raw": [
            ("gpt-4o-mini", "fast, ~15x cheaper than gpt-4o"),
            ("gpt-4o", "same as primary"),
            ("gpt-4-turbo", "previous generation"),
        ],
        "aigocode-raw": [
            ("gpt-4o-mini", "fast, low cost"),
            ("gpt-4.1-mini", "balanced"),
            ("", "same as primary"),
        ],
    }
    aux = aux_choices.get(provider_name, [("", "same as primary")])
    for i, (m, note) in enumerate(aux, 1):
        label = m or model
        rec = "  [recommended]" if i == 1 else ""
        print(f"  {i}. {label}  ({note}){rec}")
    print(f"  {len(aux) + 1}. Custom...")
    print(f"\n  Used for background tasks — does not affect conversation quality.")
    try:
        raw_aux = input("\nAux model [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    cheap_model = ""
    if raw_aux == "" or raw_aux == "1":
        cheap_model = aux[0][0] or ""
    elif raw_aux.isdigit() and 2 <= int(raw_aux) <= len(aux):
        cheap_model = aux[int(raw_aux) - 1][0] or ""
    elif raw_aux == str(len(aux) + 1):
        try:
            cheap_model = input("Custom aux model name: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
    elif not raw_aux.isdigit():
        cheap_model = raw_aux

    sections: dict[str, dict] = {
        "agent": {"model": model},
        "provider": {"name": provider_name},
    }
    if cheap_model and cheap_model != model:
        sections["agent"]["cheap_model"] = cheap_model
    if api_key:
        sections["provider"]["api_key"] = api_key

    print(f"\n{_hr}")
    try:
        write_config_toml(cfg_path, sections)
    except OSError as e:
        print(f"[Error] Could not write config: {e}", file=sys.stderr)
        return 1

    print(f"Config written to:\n  {cfg_path}")

    if api_key and provider_name not in ("ollama",):
        import os
        env_var = _provider_env_var(provider_name)
        os.environ[env_var] = api_key
        os.environ["HUSHCLAW_MODEL"] = model
        print("\nTesting connection...", end=" ", flush=True)
        try:
            from hushclaw.config.loader import load_config
            from hushclaw.providers.registry import get_provider
            cfg = load_config()
            provider = get_provider(cfg.provider)
            from hushclaw.providers.base import Message
            test_msg = [Message(role="user", content="hi")]
            resp = asyncio.run(provider.complete(
                messages=test_msg, model=model, max_tokens=10
            ))
            print("OK")
        except Exception as e:
            print(f"FAILED\n  [Warning] {e}")
            print("  You can still use hushclaw — check your API key if issues persist.")

    print(f"\nRun `hushclaw` to start chatting!\n")
    return 0


def cmd_doctor(args) -> int:
    """Check configuration, environment, and network health."""
    import socket
    import shutil as _shutil

    from hushclaw.config.loader import load_config, validate_config

    _hr = "─" * 42
    print(f"\nHushClaw Doctor\n{_hr}")

    try:
        config = load_config()
        print("✓ Config loaded")
    except Exception as e:
        print(f"✗ Config error: {e}")
        return 1

    warnings = validate_config(config)
    for w in warnings:
        icon = "✗" if w.startswith("[ERROR]") else "⚠" if w.startswith("[WARN]") else "ℹ"
        print(f"{icon} {w}")
    if not warnings:
        print("✓ Config validation passed")

    for b in ["git"]:
        if _shutil.which(b):
            print(f"✓ {b} found in PATH")
        else:
            print(f"⚠ {b} not found in PATH (needed for skill install)")

    provider = config.provider.name
    host_map = {
        "anthropic-raw": "api.anthropic.com",
        "anthropic-sdk": "api.anthropic.com",
        "openai-raw":    "api.openai.com",
        "openai-sdk":    "api.openai.com",
    }
    host = host_map.get(provider)
    if host:
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo(host, 443)
            print(f"✓ DNS resolved: {host}")
        except OSError:
            print(f"⚠ Cannot resolve {host} — check internet connection")
    else:
        print(f"ℹ Provider '{provider}' — skipping network check")

    data_dir = config.memory.data_dir
    if data_dir:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".doctor_write_test"
            test_file.touch()
            test_file.unlink()
            print(f"✓ data_dir writable: {data_dir}")
        except OSError as e:
            print(f"✗ data_dir not writable: {data_dir} — {e}")

    if config.agent.workspace_dir:
        if config.agent.workspace_dir.is_dir():
            print(f"✓ workspace_dir: {config.agent.workspace_dir}")
        else:
            print(f"⚠ workspace_dir set but does not exist: {config.agent.workspace_dir}")

    print(f"\n{_hr}")
    error_count = sum(1 for w in warnings if w.startswith("[ERROR]"))
    if error_count:
        print(f"Found {error_count} error(s). Fix before using hushclaw.\n")
        return 1
    print("All checks passed.\n")
    return 0
