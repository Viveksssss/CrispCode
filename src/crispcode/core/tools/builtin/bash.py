from __future__ import annotations

import asyncio
from crispcode.core.tools.base import BaseTool, ToolResult

_DEFAULT_TIMEOUT = 60
_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB


class BashTool(BaseTool):
    name = "bash"
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "Non-interactive only — commands requiring user input will hang and time out. "
        "Prefer short, focused commands. Output is truncated at 64 KB."
    )

    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Maximum seconds to wait (default {_DEFAULT_TIMEOUT}, max 120).",
            },
        },
        "required": ["command"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """在子进程中执行 shell 命令，返回 stdout + stderr 的组合输出。"""
        command = str(params.get("command", ""))
        timeout = min(int(str(params.get("timeout", _DEFAULT_TIMEOUT))), 120)
        try:
            proc = asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    content=f"[timeout after {timeout} seconds]",
                    is_error=True,
                    error_type="timeout",
                )

        except Exception as e:
            return ToolResult(
                content=str(e),
                is_error=True,
                error_type="runtime_error",
            )

        output = stdout_bytes.decode("utf-8", errors="replace")
        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        returncode = proc.returncode or 0

        if returncode != 0:
            return ToolResult(
                content=f"[exit code {returncode}]\n{output}",
                is_error=True,
                error_type="runtime_error",
            )

        return ToolResult(content=output or "[no output]")
