from __future__ import annotations

import os
from pathlib import Path

from colorama import Fore, Style, init as colorama_init

from core.logging import append_debug_log, write_terminal_log


colorama_init()

_cli_workspace_root: str | None = None


def init_debug_logger(project_path: str, reset_existing: bool = True) -> str:
    """Initialize the one compiler log owned by the output workspace."""

    global _cli_workspace_root
    arc_dir = Path(project_path) / ".arc"
    arc_dir.mkdir(parents=True, exist_ok=True)
    log_path = arc_dir / "debug.log"
    if reset_existing and log_path.exists():
        log_path.unlink()
    os.environ["ARC_DEBUG_LOG_PATH"] = str(log_path)
    _cli_workspace_root = str(Path(project_path).expanduser().resolve())
    return str(log_path)


def print_cli_banner() -> None:
    border = "=" * 78
    print(
        "\n".join(
            [
                "",
                f"{Fore.BLUE}{border}{Style.RESET_ALL}",
                f"{Fore.CYAN}{Style.BRIGHT} ARC {Style.RESET_ALL}{Fore.WHITE}Requirement Compiler{Style.RESET_ALL}",
                f"{Fore.WHITE} Compile requirement graphs into frozen design and buildable skeletons{Style.RESET_ALL}",
                f"{Fore.BLUE}{border}{Style.RESET_ALL}",
            ]
        )
    )


def print_cli_startup(
    project_path: str,
    requirement_path: str,
    clear_all: bool,
    log_path: str | None,
    web_port: int,
) -> None:
    requirement_name = Path(requirement_path).parent.name or "requirements"
    mode_label = "clean compile" if clear_all else "compile"
    print()
    print(f"{Fore.WHITE}Session{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}mode      {Style.RESET_ALL}{mode_label}")
    print(f"  {Fore.CYAN}input     {Style.RESET_ALL}{requirement_name}")
    print(f"  {Fore.CYAN}output    {Style.RESET_ALL}{project_path}")
    print(f"  {Fore.CYAN}target    {Style.RESET_ALL}Web")
    print(f"  {Fore.CYAN}port      {Style.RESET_ALL}{web_port}")
    print(f"  {Fore.CYAN}stack     {Style.RESET_ALL}React + TypeScript + Express + SQLite")
    if log_path:
        print(f"  {Fore.CYAN}debug log {Style.RESET_ALL}{log_path}")
    print(f"{Fore.BLUE}{'-' * 78}{Style.RESET_ALL}\n")


def cli_log(
    agent_name: str,
    message: str,
    status: str | None = None,
    node_id: str | None = None,
) -> None:
    """Write every compiler event to the workspace log and terminal."""

    append_debug_log(
        agent_name,
        message,
        status=status,
        node_id=node_id,
        workspace_root=_cli_workspace_root,
    )
    write_terminal_log(agent_name, message, status=status, node_id=node_id)


def print_compilation_summary(
    result: dict[str, object],
    output_dir: str,
    elapsed_seconds: float,
) -> None:
    """Print a summary using the compiler's current result model."""

    border = "=" * 78
    if elapsed_seconds < 60:
        duration = f"{elapsed_seconds:.1f}s"
    else:
        minutes, seconds = divmod(int(elapsed_seconds), 60)
        duration = f"{minutes}m {seconds:02d}s"

    states = result.get("states")
    state_map = states if isinstance(states, dict) else {}
    failed_nodes = result.get("failed_nodes")
    failures = failed_nodes if isinstance(failed_nodes, list) else []

    print(f"\n{Fore.BLUE}{border}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Compilation Summary{Style.RESET_ALL}")
    print(f"{Fore.BLUE}{border}{Style.RESET_ALL}\n")
    print(f"{Fore.WHITE}Duration:{Style.RESET_ALL}     {duration}")
    print(f"{Fore.WHITE}Requirements:{Style.RESET_ALL} {len(state_map)}")
    if failures:
        print(f"{Fore.RED}Failed:{Style.RESET_ALL}       {', '.join(str(value) for value in failures)}")
    print(f"{Fore.WHITE}Output:{Style.RESET_ALL}       {output_dir}")

    debug_log = os.path.join(output_dir, ".arc", "debug.log")
    if os.path.exists(debug_log):
        print(f"{Fore.WHITE}Debug log:{Style.RESET_ALL}    {debug_log}")

    if result.get("ok") and failures:
        print(
            f"\n{Fore.YELLOW}Compilation completed with skipped requirements"
            f"{Style.RESET_ALL}"
        )
    elif result.get("ok"):
        print(f"\n{Fore.GREEN}Compilation successful{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.RED}Compilation failed; inspect the debug log.{Style.RESET_ALL}")
    print(f"{Fore.BLUE}{border}{Style.RESET_ALL}\n")
