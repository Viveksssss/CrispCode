from __future__ import annotations

import logging

from crispcode.core.config import McpServerConfig
from crispcode.core.mcp.client import McpClient
from crispcode.core.mcp.tool import McpTool
from crispcode.core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class McpServerManager:
    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}
        self._tools: list[McpTool] = []

    async def start_all(self, servers: list[McpServerConfig]) -> None:
        """依次连接每个 MCP server，发现工具后缓存供后续 registry 使用；失败时记录日志并跳过"""
        for cfg in servers:
            try:
                client: McpClient = await self._connect(cfg)
                tool_defs = await client.list_tools()
                for tool_def in tool_defs:
                    self._tools.append(McpTool(client, cfg.name, tool_def))
                self._clients[cfg.name] = client
                logger.info(
                    "mcp: server '%s' connected, %d tool(s) discovered",
                    cfg.name,
                    len(tool_defs),
                )
            except Exception:
                logger.exception("mcp: server '%s' failed to start, skipping", cfg.name)

    def registry_tools(self, registry: ToolRegistry) -> None:
        for tool in self._tools:
            registry.register(tool)

    def get_tools(self) -> list[McpTool]:
        return list(self._tools)

    async def stop_all(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                logger.info("mcp: server '%s' closed", name)
            except Exception:
                logger.warning("mcp: error closing server '%s'", name)
        self._clients.clear()

    async def _connect(self, cfg: McpServerConfig) -> McpClient:
        """根据 transport 类型建立连接"""
        client = McpClient()
        if cfg.transport == "stdio":
            if not cfg.command:
                raise ValueError(
                    f"mcp server '{cfg.name}': stdio transport requires 'command'"
                )
            await client.connect_stdio(cfg.command, cfg.args, cfg.env or None)
        elif cfg.transport == "tcp":
            await client.connect_tcp(cfg.host, cfg.port)
        else:
            raise ValueError(
                f"mcp server '{cfg.name}': unknown transport '{cfg.transport}'"
            )
        return client
