from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from compiler import CompilationRequest, Compiler
from core.config import set_app_type, set_web_port, set_workspace_root
from core.service import configure_runtime


LogCallback = Callable[[str, str, str | None, str | None], Awaitable[None] | None]


class ARCWorkflowManager:
    """Compatibility facade from the existing CLI to the compiler interface."""

    def __init__(
        self,
        workspace_path: str,
        requirement_path: str = "",
        app_type: str = "web",
        web_port: int = 3301,
        log_cb: LogCallback | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.requirement_path = Path(requirement_path).expanduser().resolve()
        self.app_type = str(app_type or "web").strip().lower()
        self.web_port = int(web_port)
        self.log_cb = log_cb or _default_log_cb

    async def cleanup_workspace(self) -> bool:
        await self._log("Compiler", "Clear-and-recompile requested. Cleaning workspace...")
        try:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            for item in self.workspace_path.iterdir():
                if item.name == "requirements":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            return True
        except OSError as exc:
            await self._log("Compiler", f"Failed to clean workspace: {exc}", "error")
            return False

    async def start_compilation(
        self,
        *,
        clear_all: bool = False,
        resume_from_queue: bool = False,
        retry_failed: bool = False,
        retry_node_ids: list[str] | None = None,
    ) -> dict[str, object]:
        await self._log("Compiler", "ARC compilation started.")
        if clear_all and not await self.cleanup_workspace():
            return {"ok": False, "complete": False, "states": {}, "failed_nodes": []}

        self.workspace_path.mkdir(parents=True, exist_ok=True)
        set_workspace_root(self.workspace_path)
        set_app_type(self.app_type)
        set_web_port(self.web_port)
        runtime = configure_runtime(
            project_dir=str(self.workspace_path),
            app_type=self.app_type,
            web_port=self.web_port,
        )
        runtime.traceability.init_store(reset=False)
        if resume_from_queue:
            runtime.events.mark_run_resumed("ARC compiler resumed from processing queue.")
        else:
            runtime.events.mark_run_started("ARC deterministic compiler run started.")

        compiler = Compiler(runtime, self.log_cb)
        result = await compiler.compile(
            CompilationRequest(
                requirement_path=self.requirement_path,
                output_dir=self.workspace_path,
                app_type=self.app_type,
                web_port=self.web_port,
                resume=resume_from_queue,
                retry_failed=retry_failed,
                retry_node_ids=tuple(retry_node_ids or ()),
            )
        )
        if result.complete and result.ok:
            runtime.events.mark_run_completed("ARC compilation completed.")
            await self._log("Compiler", "Compilation finished successfully.")
        elif result.ok:
            runtime.events.mark_run_paused("ARC database schema completed; remaining passes are pending.")
            await self._log("Compiler", "Compilation paused after the implemented DATABASE_SCHEMA pass.", "warning")
        else:
            runtime.events.mark_run_failed("ARC compiler pass failed.")
            await self._log("Compiler", "Compilation failed; inspect the compiler log.", "error")
        return result.to_dict()

    async def _log(
        self,
        agent_name: str,
        message: str,
        status: str | None = None,
        node_id: str | None = None,
    ) -> None:
        result = self.log_cb(agent_name, message, status, node_id)
        if inspect.isawaitable(result):
            await result


async def _default_log_cb(
    agent_name: str,
    message: str,
    status: str | None = None,
    node_id: str | None = None,
) -> None:
    from core.logging import append_debug_log, write_terminal_log

    append_debug_log(agent_name, message, status=status, node_id=node_id)
    write_terminal_log(agent_name, message, status=status, node_id=node_id)
