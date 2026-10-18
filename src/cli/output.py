from __future__ import annotations

from pathlib import Path
import os

from colorama import Fore, Style, init as colorama_init

from utils.logging import append_debug_log, write_terminal_log


colorama_init()

_cli_workspace_root: str | None = None


def init_debug_logger(project_path: str) -> Path:
    """Append to the workspace log without deleting previous run output."""
    global _cli_workspace_root
    _cli_workspace_root = str(Path(project_path).expanduser().resolve())
    os.environ['ARC_WORKSPACE_ROOT'] = _cli_workspace_root
    path = Path(_cli_workspace_root) / '.arc/debug.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path

def _print(message: str = "") -> None:
    append_debug_log(message, workspace_root=_cli_workspace_root)
    print(message)


def print_cli_banner() -> None:
    border = "=" * 78
    _print(
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
    web_port: int,
) -> None:
    requirement_name = Path(requirement_path).parent.name or "requirements"
    mode_label = "clean compile" if clear_all else "compile"
    _print()
    _print(f"{Fore.WHITE}Session{Style.RESET_ALL}")
    _print(f"  {Fore.CYAN}mode      {Style.RESET_ALL}{mode_label}")
    _print(f"  {Fore.CYAN}input     {Style.RESET_ALL}{requirement_name}")
    _print(f"  {Fore.CYAN}output    {Style.RESET_ALL}{project_path}")
    _print(f"  {Fore.CYAN}target    {Style.RESET_ALL}Web")
    _print(f"  {Fore.CYAN}port      {Style.RESET_ALL}{web_port}")
    _print(f"  {Fore.CYAN}stack     {Style.RESET_ALL}React + TypeScript + Express + SQLite")
    _print(f"  {Fore.CYAN}debug log {Style.RESET_ALL}{Path(project_path) / '.arc/debug.log'}")
    _print(f"{Fore.BLUE}{'-' * 78}{Style.RESET_ALL}\n")


def cli_log(
    agent_name: str,
    message: str,
    status: str | None = None,
    node_id: str | None = None,
) -> None:
    """Synchronize compiler events to the console and workspace debug.log."""

    write_terminal_log(agent_name, message, status=status, node_id=node_id,
                       workspace_root=_cli_workspace_root)


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
    failures = {str(rid) for rid in failed_nodes} if isinstance(failed_nodes, list) else set()
    failures.update(str(rid) for rid, state in state_map.items()
                    if str(state).startswith(('FAILED', 'SKIPPED', 'BLOCKED')))
    completed = result.get('completed', result.get('ok', False))

    _print(f"\n{Fore.BLUE}{border}{Style.RESET_ALL}")
    status = 'Compilation completed' if completed else 'Compilation failed'
    _print(f"{Fore.CYAN}{status}{Style.RESET_ALL}")
    _print(f"{Fore.BLUE}{border}{Style.RESET_ALL}\n")
    _print(f"{Fore.WHITE}Duration:{Style.RESET_ALL}     {duration}")
    _print(f"{Fore.WHITE}Requirements:{Style.RESET_ALL} {len(state_map)}")
    skipped = ', '.join(sorted(failures)) if failures else 'None'
    _print(f"{Fore.YELLOW}SKIPPED:{Style.RESET_ALL}      {skipped}")
    _print(f"{Fore.WHITE}Output:{Style.RESET_ALL}       {output_dir}")
    _print(f"{Fore.WHITE}Debug log:{Style.RESET_ALL}    {Path(output_dir) / '.arc/debug.log'}")

    if failures or not completed or not result.get('ok'):
        _print(f"{Fore.WHITE}Details:{Style.RESET_ALL}      .arc/workflow/report.json")
    _print(f"{Fore.BLUE}{border}{Style.RESET_ALL}\n")
