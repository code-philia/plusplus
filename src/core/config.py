"""Configuration validation and health check for ARC."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from colorama import Fore, Style

def set_workspace_root(path: str | os.PathLike[str]) -> None:
    """Publish the active output workspace for compiler logging."""

    os.environ["ARC_WORKSPACE_ROOT"] = str(Path(path).expanduser().resolve())


def check_config() -> dict[str, Any]:
    """
    Validate ARC configuration and environment.

    Returns a dict with:
      - "ok": bool (overall health)
      - "errors": list of critical issues
      - "warnings": list of non-critical issues
      - "info": list of informational messages
    """
    errors = []
    warnings = []
    info = []

    # A full compile executes model-backed Database and Design passes. Doctor
    # reports missing values as warnings so configuration can still be inspected.
    model_vars = {
        "OPENAI_API_KEY": "Main API key for model inference",
        "OPENAI_BASE_URL": "API base URL",
        "MODEL": "Main coding model name",
    }

    for var, description in model_vars.items():
        value = os.environ.get(var, "").strip()
        if not value:
            warnings.append(f"Model pass not configured: {var} ({description})")
        elif var == "OPENAI_API_KEY" and value.startswith("sk-your-"):
            warnings.append(f"{var} still contains placeholder value")

    # Check optional visual model
    visual_key = os.environ.get("VISUAL_API_KEY", "").strip()
    visual_model = os.environ.get("VISUAL_MODEL", "").strip()
    effective_visual_key = visual_key or os.environ.get("OPENAI_API_KEY", "").strip()
    effective_visual_model = visual_model or os.environ.get("MODEL", "").strip()
    if (visual_key or visual_model) and not effective_visual_key:
        warnings.append("Visual analysis has no VISUAL_API_KEY or OPENAI_API_KEY")
    if (visual_key or visual_model) and not effective_visual_model:
        warnings.append("Visual analysis has no VISUAL_MODEL or MODEL")

    visual_timeout = os.environ.get("ARC_VISUAL_TIMEOUT_SECONDS", "120").strip()
    try:
        if float(visual_timeout) <= 0:
            warnings.append("ARC_VISUAL_TIMEOUT_SECONDS must be positive")
    except ValueError:
        warnings.append(
            f"ARC_VISUAL_TIMEOUT_SECONDS must be numeric, got: {visual_timeout}"
        )

    # Check retry count
    retry_count = os.environ.get("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", "2").strip()
    try:
        count = int(retry_count)
        if count < 0 or count > 10:
            warnings.append(f"ARC_STRUCTURED_OUTPUT_RETRY_COUNT={count} is unusual (recommended: 1-5)")
    except ValueError:
        warnings.append(f"ARC_STRUCTURED_OUTPUT_RETRY_COUNT must be an integer, got: {retry_count}")

    # Check .env file presence
    env_file = Path(".env")
    if not env_file.exists():
        warnings.append("No .env file found in current directory (copy .env_example to .env)")
    else:
        info.append(f"Configuration loaded from {env_file.resolve()}")

    # Check Python version
    if sys.version_info < (3, 11):
        errors.append(f"Python 3.11+ required, found: {sys.version_info.major}.{sys.version_info.minor}")
    else:
        info.append(f"Python version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    info.append("ARC compiler: available")

    # Check Node.js for generated Web workspaces.
    import shutil
    node_path = shutil.which("node")
    if node_path:
        info.append(f"Node.js: available at {node_path}")
    else:
        warnings.append("Node.js not found (required for Web project initialization and build)")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "info": info,
    }


def print_health_check() -> int:
    """
    Print configuration health check to terminal.
    Returns exit code: 0 if ok, 1 if errors found.
    """
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}ARC Configuration Health Check{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}\n")

    result = check_config()

    if result["info"]:
        print(f"{Fore.WHITE}ℹ Info{Style.RESET_ALL}")
        for msg in result["info"]:
            print(f"  {Fore.WHITE}•{Style.RESET_ALL} {msg}")
        print()

    if result["warnings"]:
        print(f"{Fore.YELLOW}⚠ Warnings{Style.RESET_ALL}")
        for msg in result["warnings"]:
            print(f"  {Fore.YELLOW}•{Style.RESET_ALL} {msg}")
        print()

    if result["errors"]:
        print(f"{Fore.RED}✗ Errors{Style.RESET_ALL}")
        for msg in result["errors"]:
            print(f"  {Fore.RED}•{Style.RESET_ALL} {msg}")
        print()

    if result["ok"]:
        print(f"{Fore.GREEN}✓ Configuration is valid{Style.RESET_ALL}\n")
        return 0
    else:
        print(f"{Fore.RED}✗ Configuration has errors. Fix them before running ARC.{Style.RESET_ALL}\n")
        print(f"{Fore.WHITE}Quick fix:{Style.RESET_ALL}")
        print(f"  1. Copy .env_example to .env")
        print(f"  2. Edit .env and fill in your API credentials")
        print(f"  3. Run: arc doctor\n")
        return 1


def interactive_config_setup() -> int:
    """
    Interactively create or update .env file with core configuration.
    Returns exit code: 0 on success, 1 on user cancellation.
    """
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}ARC Configuration Setup{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}\n")

    env_file = Path(".env")

    if env_file.exists():
        print(f"{Fore.YELLOW}⚠ .env file already exists{Style.RESET_ALL}")
        overwrite = input("Overwrite existing values? (y/N): ").strip().lower()
        if overwrite not in {"y", "yes"}:
            print(f"{Fore.YELLOW}Configuration cancelled.{Style.RESET_ALL}\n")
            return 1
        print()

    # Read existing .env if present
    existing_config = {}
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    existing_config[key.strip()] = value.strip()
        except Exception:
            pass

    print(f"{Fore.WHITE}Enter configuration values (press Enter to keep existing):{Style.RESET_ALL}\n")

    # Collect required values
    configs = {}

    # OPENAI_API_KEY
    existing_key = existing_config.get("OPENAI_API_KEY", "")
    if existing_key and not existing_key.startswith("sk-your-"):
        prompt = f"OpenAI API Key [{existing_key[:8]}...{existing_key[-4:]}]: "
    else:
        prompt = "OpenAI API Key: "

    api_key = input(prompt).strip()
    if api_key:
        configs["OPENAI_API_KEY"] = api_key
    elif existing_key and not existing_key.startswith("sk-your-"):
        configs["OPENAI_API_KEY"] = existing_key
    else:
        print(f"{Fore.RED}✗ API key is required{Style.RESET_ALL}\n")
        return 1

    # OPENAI_BASE_URL
    existing_base = existing_config.get("OPENAI_BASE_URL", "")
    default_base = existing_base if existing_base else "https://api.openai.com/v1"
    base_url = input(f"OpenAI Base URL [{default_base}]: ").strip()
    configs["OPENAI_BASE_URL"] = base_url if base_url else default_base

    # MODEL
    existing_model = existing_config.get("MODEL", "")
    default_model = existing_model if existing_model else "gpt-4o"
    model = input(f"Model name [{default_model}]: ").strip()
    configs["MODEL"] = model if model else default_model

    # Merge with existing config
    final_config = {**existing_config, **configs}

    # Write .env file
    lines = []
    lines.append("# ARC Configuration")
    lines.append("# Generated by: arc config")
    lines.append("")
    lines.append("# Required: OpenAI-compatible API")
    lines.append(f"OPENAI_API_KEY={final_config['OPENAI_API_KEY']}")
    lines.append(f"OPENAI_BASE_URL={final_config['OPENAI_BASE_URL']}")
    lines.append(f"MODEL={final_config['MODEL']}")
    lines.append("")

    # Preserve other existing keys
    core_keys = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL"}
    other_keys = {k: v for k, v in existing_config.items() if k not in core_keys}

    if other_keys:
        lines.append("# Other configuration")
        for key, value in sorted(other_keys.items()):
            lines.append(f"{key}={value}")
        lines.append("")

    try:
        env_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n{Fore.GREEN}✓ Configuration saved to {env_file.resolve()}{Style.RESET_ALL}\n")
        print(f"{Fore.WHITE}Next steps:{Style.RESET_ALL}")
        print(f"  1. Run: arc doctor")
        print(f"  2. Run: arc compile <input> -o <output>\n")
        return 0
    except Exception as e:
        print(f"\n{Fore.RED}✗ Failed to write .env file: {e}{Style.RESET_ALL}\n")
        return 1
