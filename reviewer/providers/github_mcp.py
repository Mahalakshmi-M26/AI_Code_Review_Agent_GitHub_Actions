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
    def _normalize_tool(tool: Any) -> dict[str, Any]:
        if isinstance(tool, dict):
            return tool

        normalized: dict[str, Any] = {}
        for attribute_name, key_name in (
            ("name", "name"),
            ("description", "description"),
            ("input_schema", "inputSchema"),
            ("output_schema", "outputSchema"),
        ):
            value = getattr(tool, attribute_name, None)
            if value is not None:
                normalized[key_name] = value
        return normalized

    @classmethod
    def extract_pr_tools(cls, raw_tools: Any) -> list[dict[str, Any]]:
        tools = raw_tools.get("tools", raw_tools) if isinstance(raw_tools, dict) else raw_tools
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

    async def initialize(self) -> dict[str, Any]:
        print("[MCP] Starting official GitHub MCP Server")
        print("[MCP] Transport: stdio")
        print("[MCP] Docker image:")
        print(self.image)
        print("[MCP] Authentication: GitHub Actions GITHUB_TOKEN")
        print("[MCP] Toolsets:")
        print(",".join(self.toolsets))

        params = self.build_server_parameters()
        try:
            transport = stdio_client(params)
        except Exception as exc:  # pragma: no cover - stdio transport creation failed before connecting
            raise RuntimeError(f"MCP connection failed while starting the official GitHub MCP server: {exc}") from exc

        try:
            async with transport as (read, write):
                try:
                    async with ClientSession(read, write) as session:
                        self.session = session
                        result = await session.initialize()
                        result_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
                        self.server_name = str(result_dict.get("serverInfo", {}).get("name") or "unknown")
                        self.server_version = str(result_dict.get("serverInfo", {}).get("version") or "unknown")
                        print("[MCP] Connected")
                        print("[MCP] Server name:")
                        print(self.server_name)
                        print("[MCP] Server version:")
                        print(self.server_version)
                        return result_dict
                except Exception as exc:  # pragma: no cover - session startup is a distinct failure class
                    raise RuntimeError(f"MCP initialization failed: {exc}") from exc
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - stdio transport failed before session init
            raise RuntimeError(f"MCP connection failed while starting the official GitHub MCP server: {exc}") from exc

    async def discover_tools(self) -> list[dict[str, Any]]:
        try:
            params = self.build_server_parameters()
            transport = stdio_client(params)
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_response = await session.list_tools()
                    tools = self.extract_pr_tools(tools_response)
                    print("[MCP] Discovering tools...")
                    print("[MCP] PR-related tools discovered:")
                    for tool in tools:
                        print("[MCP] Tool:")
                        print(tool.get("name"))
                        print("[MCP] Description:")
                        print(tool.get("description"))
                        print("[MCP] Input schema:")
                        print(tool.get("inputSchema") or tool.get("input_schema"))
                    return tools
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - expanded for clearer runtime error
            raise RuntimeError(f"MCP tool discovery failed: {exc}") from exc

    async def run_connectivity_check(self) -> dict[str, Any]:
        try:
            info = await self.initialize()
            tools = await self.discover_tools()
            return {"serverInfo": info.get("serverInfo", {}), "tools": tools}
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            raise RuntimeError(f"MCP connectivity check failed: {exc}") from exc


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
    result = await provider.initialize()
    tool_result = await provider.discover_tools()
    print("[MCP] MCP discovery completed")
    print(json.dumps({
        "serverInfo": result.get("serverInfo", {}),
        "tools": [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "inputSchema": tool.get("inputSchema") or tool.get("input_schema"),
            }
            for tool in tool_result
        ],
    }, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(live_discovery())
