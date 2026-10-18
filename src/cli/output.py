from __future__ import annotations

from pathlib import Path

from colorama import Fore, Style, init as colorama_init

from utils.logging import write_terminal_log


colorama_init()

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
    print(f"{Fore.BLUE}{'-' * 78}{Style.RESET_ALL}\n")


def cli_log(
    agent_name: str,
    message: str,
    status: str | None = None,
    node_id: str | None = None,
) -> None:
    """Print compiler progress; model calls are retained separately in model_logs."""

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

    if result.get("ok") and failures:
        print(
            f"\n{Fore.YELLOW}Compilation completed with skipped requirements"
            f"{Style.RESET_ALL}"
        )
    elif result.get("ok"):
        print(f"\n{Fore.GREEN}Compilation successful{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.RED}Compilation failed; inspect .arc/workflow/report.json and .arc/model_logs.{Style.RESET_ALL}")
    print(f"{Fore.BLUE}{border}{Style.RESET_ALL}\n")
