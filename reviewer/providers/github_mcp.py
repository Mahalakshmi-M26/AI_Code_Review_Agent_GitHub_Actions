from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Sequence

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class GitHubMCPProvider:
    """Minimal MCP connectivity and discovery provider for the official GitHub MCP server."""

    DEFAULT_IMAGE = "ghcr.io/github/github-mcp-server"
    DEFAULT_TOOLSETS = ("repos", "pull_requests")

    def __init__(
        self,
        token: str | None = None,
        toolsets: Sequence[str] | None = None,
        image: str = DEFAULT_IMAGE,
    ) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.toolsets = tuple(toolsets or self.DEFAULT_TOOLSETS)
        self.image = image
        self.server_name: str | None = None
        self.server_version: str | None = None
        self.session: ClientSession | None = None

    def build_server_parameters(self) -> StdioServerParameters:
        env = os.environ.copy()
        if self.token:
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = self.token
        env["GITHUB_TOOLSETS"] = ",".join(self.toolsets)
        return StdioServerParameters(
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "-e",
                "GITHUB_TOOLSETS",
                self.image,
            ],
            env=env,
        )

    @staticmethod
    def _tool_field(tool: Any, *names: str) -> Any:
        for name in names:
            value = getattr(tool, name, None)
            if value is not None:
                return value
        if isinstance(tool, dict):
            for name in names:
                value = tool.get(name)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _normalize_tool(tool: Any) -> dict[str, Any]:
        if isinstance(tool, dict):
            return tool
        return {
            "name": GitHubMCPProvider._tool_field(tool, "name"),
            "description": GitHubMCPProvider._tool_field(tool, "description"),
            "inputSchema": GitHubMCPProvider._tool_field(tool, "input_schema", "inputSchema"),
            "outputSchema": GitHubMCPProvider._tool_field(tool, "output_schema", "outputSchema"),
        }

    @classmethod
    def extract_pr_tools(cls, raw_tools: Any) -> list[dict[str, Any]]:
        tools = raw_tools.tools if hasattr(raw_tools, "tools") else raw_tools
        if isinstance(tools, dict):
            tools = tools.get("tools", [])
        if not isinstance(tools, list):
            raise RuntimeError("MCP tool discovery returned an unrecognized payload.")

        relevant: list[dict[str, Any]] = []
        for tool in tools:
            normalized = cls._normalize_tool(tool)
            name = str(normalized.get("name") or "")
            description = str(normalized.get("description") or "")
            lowered_name = name.lower()
            lowered_desc = description.lower()
            if (
                "pull" in lowered_name
                or "review" in lowered_name
                or "pull" in lowered_desc
                or "review" in lowered_desc
                or "repository" in lowered_name
                or "diff" in lowered_name
                or "file" in lowered_name
            ):
                relevant.append(normalized)
        return relevant

    @staticmethod
    def _extract_server_info(result: Any) -> tuple[str | None, str | None]:
        if result is None:
            return None, None

        if hasattr(result, "server_info"):
            info = result.server_info
            if info is not None:
                return str(getattr(info, "name", None) or "unknown"), str(getattr(info, "version", None) or "unknown")

        if hasattr(result, "serverInfo"):
            info = result.serverInfo
            if info is not None:
                return str(getattr(info, "name", None) or "unknown"), str(getattr(info, "version", None) or "unknown")

        if isinstance(result, dict):
            info = result.get("serverInfo") or result.get("server_info") or {}
            if isinstance(info, dict):
                return str(info.get("name") or "unknown"), str(info.get("version") or "unknown")

        return None, None

    async def _run_session(self) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
        params = self.build_server_parameters()
        try:
            transport = stdio_client(params)
        except Exception as exc:  # pragma: no cover - stdio transport creation failed before connecting
            raise RuntimeError(f"MCP connection failed while starting the official GitHub MCP server: {exc}") from exc

        try:
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    try:
                        init_result = await session.initialize()
                    except Exception as exc:  # pragma: no cover - session startup is a distinct failure class
                        raise RuntimeError(f"MCP initialization failed: {exc}") from exc

                    server_name, server_version = self._extract_server_info(init_result)
                    self.server_name = server_name or "unknown"
                    self.server_version = server_version or "unknown"
                    print("[MCP] Connected")
                    print("[MCP] Server name:")
                    print(self.server_name)
                    print("[MCP] Server version:")
                    print(self.server_version)

                    try:
                        tools_response = await session.list_tools()
                    except Exception as exc:  # pragma: no cover - tool discovery is explicit failure reporting
                        raise RuntimeError(f"MCP tool discovery failed while calling list_tools(): {exc}") from exc

                    response_type = type(tools_response)
                    print("[MCP] list_tools response type:")
                    print(f"{response_type.__module__}.{response_type.__qualname__}")
                    if hasattr(tools_response, "tools"):
                        attr_names = [name for name in dir(tools_response) if not name.startswith("_") and name in {"tools", "meta", "ttl_ms", "cache_scope", "next_cursor", "result_type"}]
                        print("[MCP] list_tools response attributes:")
                        print(attr_names)

                    raw_tools = getattr(tools_response, "tools", None)
                    if raw_tools is None and isinstance(tools_response, dict):
                        raw_tools = tools_response.get("tools", [])
                    if not isinstance(raw_tools, list):
                        raw_tools = []

                    print("[MCP] Discovering tools...")
                    for tool in raw_tools:
                        normalized = self._normalize_tool(tool)
                        print("[MCP] Tool:")
                        print(normalized.get("name"))
                        print("[MCP] Description:")
                        print(normalized.get("description"))
                        print("[MCP] Input schema:")
                        print(normalized.get("inputSchema") or normalized.get("input_schema"))

                    tools = self.extract_pr_tools(tools_response)
                    return init_result, tools, {"server_name": self.server_name, "server_version": self.server_version}
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - stdio transport failed before session init
            raise RuntimeError(f"MCP connection failed while starting the official GitHub MCP server: {exc}") from exc

    async def initialize(self) -> dict[str, Any]:
        print("[MCP] Starting official GitHub MCP Server")
        print("[MCP] Transport: stdio")
        print("[MCP] Docker image:")
        print(self.image)
        print("[MCP] Authentication: GitHub Actions GITHUB_TOKEN")
        print("[MCP] Toolsets:")
        print(",".join(self.toolsets))

        init_result, _, _ = await self._run_session()
        if hasattr(init_result, "model_dump"):
            return init_result.model_dump()
        return dict(init_result)

    async def discover_tools(self) -> list[dict[str, Any]]:
        _, tools, _ = await self._run_session()
        return tools

    async def run_connectivity_check(self) -> dict[str, Any]:
        init_result, tools, server_info = await self._run_session()
        return {"serverInfo": init_result.model_dump() if hasattr(init_result, "model_dump") else dict(init_result), "tools": tools, "server_info": server_info}


async def live_discovery() -> None:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN for official GitHub MCP live discovery.")

    print("[MCP] Docker available:")
    print("docker available")
    print("[MCP] Starting:")
    print("ghcr.io/github/github-mcp-server")
    print("[MCP] Transport:")
    print("stdio")
    print("[MCP] Toolsets:")
    print("repos,pull_requests")
    print("[MCP] Authentication:")
    print("GitHub Actions GITHUB_TOKEN")

    provider = GitHubMCPProvider(token=token)
    init_result, tools, _ = await provider._run_session()
    print("[MCP] MCP discovery completed")
    server_info = init_result.model_dump() if hasattr(init_result, "model_dump") else dict(init_result)
    print(json.dumps({
        "serverInfo": server_info,
        "tools": [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "inputSchema": tool.get("inputSchema") or tool.get("input_schema"),
            }
            for tool in tools
        ],
    }, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(live_discovery())
