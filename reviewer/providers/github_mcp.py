from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from typing import Any, Sequence

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class GitHubMCPProvider:
    """Connectivity, discovery, and Pull Request reads via the official GitHub MCP server."""

    DEFAULT_IMAGE = "ghcr.io/github/github-mcp-server"
    DEFAULT_TOOLSETS = ("repos", "pull_requests")
    FILES_PAGE_SIZE = 100

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

    @staticmethod
    def _result_error(result: Any) -> bool:
        if isinstance(result, dict):
            return bool(result.get("isError", result.get("is_error", False)))
        return bool(getattr(result, "is_error", getattr(result, "isError", False)))

    @staticmethod
    def _result_content(result: Any) -> Any:
        if isinstance(result, dict):
            structured = result.get("structuredContent", result.get("structured_content"))
        else:
            structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
        if structured is not None:
            return structured

        content = result.get("content", []) if isinstance(result, dict) else getattr(result, "content", [])
        text_parts = []
        for item in content or []:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
        if not text_parts:
            raise RuntimeError("MCP tool returned neither structured content nor text content.")
        return "\n".join(text_parts)

    @classmethod
    def _decode_json_payload(cls, payload: Any, label: str) -> Any:
        if isinstance(payload, (dict, list)):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"MCP {label} response did not contain valid JSON.") from exc
        raise RuntimeError(f"MCP {label} response had an unsupported payload shape.")

    @classmethod
    def _parse_files_result(cls, result: Any) -> list[dict[str, Any]]:
        if cls._result_error(result):
            raise RuntimeError("MCP pull_request_read(method=get_files) returned a tool error.")
        payload = cls._decode_json_payload(cls._result_content(result), "get_files")
        if isinstance(payload, dict):
            payload = payload.get("files")
        if not isinstance(payload, list):
            raise RuntimeError("MCP get_files response did not contain a files list.")

        normalized: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise RuntimeError("MCP get_files response contained a malformed file entry.")
            filename = item.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                raise RuntimeError("MCP get_files response contained a file without a filename.")
            patch = item.get("patch")
            normalized_item = dict(item)
            normalized_item["filename"] = filename.strip()
            normalized_item["patch"] = patch if isinstance(patch, str) else ""
            normalized.append(normalized_item)
        return normalized

    @staticmethod
    def _diff_filename(header: str) -> str | None:
        for prefix in ("+++ ", "--- "):
            marker = header.find(prefix)
            if marker >= 0:
                path = header[marker + len(prefix):].splitlines()[0].strip()
                if path == "/dev/null":
                    continue
                if path.startswith(("a/", "b/")):
                    return path[2:]

        first_line = header.splitlines()[0] if header else ""
        try:
            parts = shlex.split(first_line)
        except ValueError:
            parts = []
        if len(parts) >= 4:
            new_path = parts[3]
            old_path = parts[2]
            if new_path != "/dev/null" and new_path.startswith("b/"):
                return new_path[2:]
            if old_path != "/dev/null" and old_path.startswith("a/"):
                return old_path[2:]
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", first_line)
        return match.group(2) if match else None

    @classmethod
    def _parse_unified_diff(cls, diff_text: str) -> list[dict[str, str]]:
        blocks = re.split(r"(?m)(?=^diff --git )", diff_text.strip())
        files: list[dict[str, str]] = []
        for block in blocks:
            if not block.startswith("diff --git "):
                continue
            filename = cls._diff_filename(block)
            if not filename:
                continue
            hunk_start = re.search(r"(?m)^@@ ", block)
            if hunk_start:
                patch = block[hunk_start.start():].strip()
            else:
                patch = ""
            files.append({"filename": filename, "patch": patch})
        return files

    @classmethod
    def _parse_diff_result(cls, result: Any) -> list[dict[str, str]]:
        if cls._result_error(result):
            raise RuntimeError("MCP pull_request_read(method=get_diff) returned a tool error.")
        payload = cls._result_content(result)
        if isinstance(payload, str):
            diff_text = payload
        elif isinstance(payload, dict):
            diff_text = payload.get("diff")
        else:
            diff_text = None
        if not isinstance(diff_text, str) or not diff_text.strip():
            raise RuntimeError("MCP get_diff response did not contain unified diff text.")
        files = cls._parse_unified_diff(diff_text)
        if not files:
            raise RuntimeError("MCP get_diff response contained no parseable file diffs.")
        return files

    @staticmethod
    def _read_arguments(owner: str, repo: str, pull_number: int, method: str, page: int | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "method": method,
            "owner": owner,
            "repo": repo,
            "pullNumber": pull_number,
        }
        if page is not None:
            arguments.update({"page": page, "perPage": GitHubMCPProvider.FILES_PAGE_SIZE})
        return arguments

    async def get_pull_request_files(self, owner: str, repo: str, pull_number: int) -> list[dict[str, Any]]:
        """Read changed files through MCP and ensure each reviewable file has patch text."""
        if not self.token:
            raise RuntimeError("Missing GITHUB_TOKEN for GitHub MCP Pull Request reads.")

        print("[MCP] Starting official GitHub MCP Server")
        print("[MCP] Reading Pull Request changed files")
        params = self.build_server_parameters()
        try:
            transport = stdio_client(params)
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    try:
                        await session.initialize()
                    except Exception as exc:
                        raise RuntimeError(f"MCP initialization failed: {exc}") from exc

                    print("[MCP] Tool:")
                    print("pull_request_read")
                    print("[MCP] Method:")
                    print("get_files")
                    files: list[dict[str, Any]] = []
                    page = 1
                    while True:
                        try:
                            result = await session.call_tool(
                                "pull_request_read",
                                self._read_arguments(owner, repo, pull_number, "get_files", page),
                            )
                        except Exception as exc:
                            raise RuntimeError(f"MCP get_files call failed: {exc}") from exc
                        page_files = self._parse_files_result(result)
                        files.extend(page_files)
                        if len(page_files) < self.FILES_PAGE_SIZE:
                            break
                        page += 1

                    print("[MCP] Changed files returned:")
                    print(len(files))
                    if files and all(item["patch"].strip() for item in files):
                        print("[MCP] GitHub changed-file READ completed")
                        return files

                    print("[MCP] get_files did not provide usable patch content")
                    print("[MCP] Falling back to MCP method:")
                    print("get_diff")
                    try:
                        diff_result = await session.call_tool(
                            "pull_request_read",
                            self._read_arguments(owner, repo, pull_number, "get_diff"),
                        )
                    except Exception as exc:
                        raise RuntimeError(f"MCP get_diff call failed: {exc}") from exc
                    diff_files = self._parse_diff_result(diff_result)
                    print("[MCP] Unified PR diff received")
                    by_filename = {item["filename"]: item["patch"] for item in diff_files}
                    if files:
                        for item in files:
                            if not item["patch"]:
                                item["patch"] = by_filename.get(item["filename"], "")
                        normalized = files
                    else:
                        normalized = diff_files
                    if not any(item["patch"].strip() for item in normalized):
                        raise RuntimeError("MCP returned no usable changed-file patches.")
                    print("[MCP] Diff normalized into:")
                    print(len(normalized))
                    print("[MCP] GitHub changed-file READ completed")
                    return normalized
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"MCP connection failed while reading Pull Request files: {exc}") from exc

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
