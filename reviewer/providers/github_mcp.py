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
        first_line = header.splitlines()[0] if header else ""
        try:
            parts = shlex.split(first_line)
        except ValueError:
            parts = []
        if len(parts) >= 4:
            new_path = parts[3]
            if new_path != "/dev/null" and new_path.startswith("b/"):
                return new_path[2:]
        rename_to = re.search(r"(?m)^rename to (.+)$", header)
        if rename_to:
            try:
                path_parts = shlex.split(rename_to.group(1))
            except ValueError:
                path_parts = []
            if path_parts:
                return path_parts[0]

        for prefix in ("+++ ", "--- "):
            match = re.search(rf"(?m)^{re.escape(prefix)}(.+)$", header)
            if not match:
                continue
            try:
                path_parts = shlex.split(match.group(1))
            except ValueError:
                path_parts = []
            if not path_parts or path_parts[0] == "/dev/null":
                continue
            path = path_parts[0]
            if path.startswith(("a/", "b/")):
                return path[2:]
            return path

        if len(parts) >= 3 and parts[2] != "/dev/null" and parts[2].startswith("a/"):
            return parts[2][2:]
        return None

    @staticmethod
    def _normalize_diff_path(path: str) -> str:
        candidate = path.strip()
        try:
            parsed = shlex.split(candidate)
        except ValueError:
            parsed = []
        if len(parsed) == 1:
            candidate = parsed[0]
        if candidate.startswith(("a/", "b/")):
            candidate = candidate[2:]
        return candidate.replace("\\", "/")

    @classmethod
    def _parse_unified_diff(cls, diff_text: str) -> list[dict[str, str]]:
        blocks = re.split(r"(?m)(?=^diff --git )", diff_text)
        files: list[dict[str, str]] = []
        for block in blocks:
            block = block.lstrip("\r\n")
            if not block.startswith("diff --git "):
                continue
            filename = cls._diff_filename(block)
            if not filename:
                continue
            hunk_start = re.search(r"(?m)^@@ ", block)
            if hunk_start:
                patch = block[hunk_start.start():].rstrip("\r\n")
            else:
                patch = ""
            rename_from = re.search(r"(?m)^rename from (.+)$", block)
            rename_to = re.search(r"(?m)^rename to (.+)$", block)
            files.append({
                "filename": filename,
                "patch": patch,
                "rename_only": not hunk_start and rename_from is not None and rename_to is not None,
            })
        return files

    @classmethod
    def _log_diff_normalization_diagnostics(
        cls,
        diff_text: str,
        metadata: list[dict[str, Any]],
    ) -> None:
        allowed_headers = (
            "diff --git ",
            "index ",
            "--- ",
            "+++ ",
            "new file mode ",
            "deleted file mode ",
            "similarity index ",
            "rename from ",
            "rename to ",
            "old mode ",
            "new mode ",
        )
        print("[MCP-DIFF-DIAG] Unified diff structural headers:")
        blocks = re.split(r"(?m)(?=^diff --git )", diff_text)
        for block in blocks:
            for line in block.lstrip("\r\n").splitlines():
                if line.startswith("@@ "):
                    break
                if line.startswith(allowed_headers):
                    print(f"[MCP-DIFF-DIAG] {line}")

        print("[MCP-DIFF-DIAG] Metadata files:")
        for item in metadata:
            print("[MCP-DIFF-DIAG] Metadata file:")
            print(f"[MCP-DIFF-DIAG] {item.get('filename', '')}")
            print("[MCP-DIFF-DIAG] Metadata status:")
            print(f"[MCP-DIFF-DIAG] {item.get('status', '<not provided>')}")

        sections: list[dict[str, Any]] = []
        for block in blocks:
            block = block.lstrip("\r\n")
            if not block.startswith("diff --git "):
                continue
            header = block.splitlines()[0]
            old_path = None
            new_path = None
            for line in block.splitlines():
                if line.startswith("@@ "):
                    break
                if line.startswith("--- "):
                    old_path = line[4:]
                elif line.startswith("+++ "):
                    new_path = line[4:]
            candidate = cls._diff_filename(block)
            has_hunk = re.search(r"(?m)^@@ ", block) is not None
            sections.append({
                "header": header,
                "old_path": old_path or "<not present>",
                "new_path": new_path or "<not present>",
                "candidate": candidate or "<unparsed>",
                "has_hunk": has_hunk,
                "patch_usable": has_hunk and bool(candidate),
            })

        print("[MCP-DIFF-DIAG] Metadata file count:")
        print(f"[MCP-DIFF-DIAG] {len(metadata)}")
        print("[MCP-DIFF-DIAG] Parsed diff section count:")
        print(f"[MCP-DIFF-DIAG] {len(sections)}")
        metadata_paths = {
            cls._normalize_diff_path(str(item.get("filename") or ""))
            for item in metadata
        }
        usable_count = sum(
            section["patch_usable"]
            and (
                not metadata_paths
                or cls._normalize_diff_path(str(section["candidate"])) in metadata_paths
            )
            for section in sections
        )
        print("[MCP-DIFF-DIAG] Usable patch count:")
        print(f"[MCP-DIFF-DIAG] {usable_count}")

        for index, section in enumerate(sections, start=1):
            print("[MCP-DIFF-DIAG] Section number:")
            print(f"[MCP-DIFF-DIAG] {index}")
            print("[MCP-DIFF-DIAG] Raw diff header:")
            print(f"[MCP-DIFF-DIAG] {section['header']}")
            print("[MCP-DIFF-DIAG] Parsed old path:")
            print(f"[MCP-DIFF-DIAG] {section['old_path']}")
            print("[MCP-DIFF-DIAG] Parsed new path:")
            print(f"[MCP-DIFF-DIAG] {section['new_path']}")
            print("[MCP-DIFF-DIAG] Candidate filename:")
            print(f"[MCP-DIFF-DIAG] {section['candidate']}")
            print("[MCP-DIFF-DIAG] Hunk header found:")
            print(f"[MCP-DIFF-DIAG] {'true' if section['has_hunk'] else 'false'}")

        for item in metadata:
            metadata_filename = str(item.get("filename") or "")
            normalized_metadata = cls._normalize_diff_path(metadata_filename)
            for section in sections:
                candidate = str(section["candidate"])
                matched = (
                    candidate != "<unparsed>"
                    and normalized_metadata == cls._normalize_diff_path(candidate)
                )
                print("[MCP-DIFF-DIAG] Metadata filename:")
                print(f"[MCP-DIFF-DIAG] {metadata_filename}")
                print("[MCP-DIFF-DIAG] Candidate section filename:")
                print(f"[MCP-DIFF-DIAG] {candidate}")
                print("[MCP-DIFF-DIAG] Match:")
                print(f"[MCP-DIFF-DIAG] {'true' if matched else 'false'}")

        for section in sections:
            if section["candidate"] != "<unparsed>":
                candidate_path = cls._normalize_diff_path(str(section["candidate"]))
                if candidate_path not in metadata_paths:
                    print("[MCP-DIFF-DIAG] Candidate section filename:")
                    print(f"[MCP-DIFF-DIAG] {section['candidate']}")
                    print("[MCP-DIFF-DIAG] Match:")
                    print("[MCP-DIFF-DIAG] false")

    @classmethod
    def _extract_diff_text(cls, result: Any) -> tuple[str, str]:
        if isinstance(result, dict):
            structured = result.get("structured_content", result.get("structuredContent"))
            content = result.get("content", [])
        else:
            structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
            content = getattr(result, "content", [])

        source = "structured_content"
        if structured is not None:
            payload = structured
        else:
            text_parts = []
            text_indexes = []
            for index, item in enumerate(content or []):
                text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
                    text_indexes.append(index)
            if not text_parts:
                raise RuntimeError("MCP get_diff response contained no structured content or text content.")
            payload = "\n".join(text_parts)
            source = ",".join(f"content[{index}].text" for index in text_indexes)

        if isinstance(payload, str):
            diff_text = payload
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                candidate = decoded.get("diff") or decoded.get("text")
                if isinstance(candidate, str):
                    diff_text = candidate
                    source = f"{source} JSON diff field"
        elif isinstance(payload, dict):
            diff_text = payload.get("diff") or payload.get("text")
        else:
            diff_text = None
        if not isinstance(diff_text, str) or not diff_text.strip():
            raise RuntimeError("MCP get_diff response did not contain unified diff text.")
        return diff_text, source

    @classmethod
    def _normalize_diff_files(
        cls,
        diff_text: str,
        metadata: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        diff_files = cls._parse_unified_diff(diff_text)
        if not diff_files:
            raise RuntimeError("MCP get_diff response contained zero recognized file sections.")
        if len(metadata) != len(diff_files):
            raise RuntimeError(
                f"MCP get_files metadata reported {len(metadata)} files but get_diff contained "
                f"{len(diff_files)} file sections."
            )
        if not metadata:
            return diff_files

        metadata_by_path = {
            cls._normalize_diff_path(str(item["filename"])): item
            for item in metadata
        }
        normalized: list[dict[str, Any]] = []
        for diff_file in diff_files:
            diff_path = cls._normalize_diff_path(diff_file["filename"])
            metadata_item = metadata_by_path.get(diff_path)
            if metadata_item is None:
                raise RuntimeError(
                    f"MCP get_diff file {diff_file['filename']!r} did not match get_files metadata."
                )
            normalized_item = dict(metadata_item)
            normalized_item["filename"] = str(metadata_item["filename"]).strip()
            normalized_item["patch"] = diff_file["patch"]
            if not diff_file["patch"]:
                if str(metadata_item.get("status", "")).lower() == "renamed" and diff_file["rename_only"]:
                    reason = "rename-only / no content changes"
                else:
                    reason = "metadata-only / no content changes"
                normalized_item["review_skip_reason"] = reason
                print(f"[MCP] Non-reviewable change: {normalized_item['filename']} - {reason}")
            normalized.append(normalized_item)
        return normalized

    @staticmethod
    def _log_diff_result_shape(result: Any) -> None:
        if isinstance(result, dict):
            structured = result.get("structured_content", result.get("structuredContent"))
            content = result.get("content", [])
        else:
            structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
            content = getattr(result, "content", [])
        print("[MCP] get_diff result type:")
        print(f"{type(result).__module__}.{type(result).__qualname__}")
        print("[MCP] structured_content present:")
        print("true" if structured is not None else "false")
        print("[MCP] content item count:")
        print(len(content or []))
        for index, item in enumerate(content or []):
            item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            print(f"[MCP] content[{index}] type: {item_type or type(item).__name__}")
            print(f"[MCP] content[{index}] text present: {'true' if isinstance(text, str) else 'false'}")
            print(f"[MCP] content[{index}] text length: {len(text) if isinstance(text, str) else 0}")

    @staticmethod
    def _exception_detail(exc: BaseException) -> str:
        if isinstance(exc, BaseExceptionGroup):
            nested = "; ".join(GitHubMCPProvider._exception_detail(item) for item in exc.exceptions)
            return f"{type(exc).__name__}: {exc}; inner: {nested}"
        return f"{type(exc).__name__}: {exc}"

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
        failure: RuntimeError | None = None
        normalized: list[dict[str, Any]] | None = None
        try:
            transport = stdio_client(params)
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    try:
                        await session.initialize()
                    except Exception as exc:
                        failure = RuntimeError(f"MCP connection initialization failure: {self._exception_detail(exc)}")

                    files: list[dict[str, Any]] = []
                    if failure is None:
                        print("[MCP] Tool:")
                        print("pull_request_read")
                        print("[MCP] Method:")
                        print("get_files")
                    page = 1
                    while failure is None:
                        try:
                            result = await session.call_tool(
                                "pull_request_read",
                                self._read_arguments(owner, repo, pull_number, "get_files", page),
                            )
                        except Exception as exc:
                            failure = RuntimeError(f"MCP get_files tool failure: {self._exception_detail(exc)}")
                            break
                        if self._result_error(result):
                            failure = RuntimeError("MCP get_files tool failure: pull_request_read returned a tool error.")
                            break
                        try:
                            page_files = self._parse_files_result(result)
                        except Exception as exc:
                            failure = RuntimeError(f"MCP get_files parse failure: {self._exception_detail(exc)}")
                            break
                        files.extend(page_files)
                        if len(page_files) < self.FILES_PAGE_SIZE:
                            break
                        page += 1

                    if failure is None:
                        print("[MCP] Changed files returned:")
                        print(len(files))
                        if not files:
                            print("[MCP] No effective Pull Request changes detected")
                            print("[MCP] get_diff skipped")
                            normalized = []
                        elif all(item["patch"].strip() for item in files):
                            normalized = files
                        else:
                            print("[MCP] get_files did not provide usable patch content")
                            print("[MCP] Falling back to MCP method:")
                            print("get_diff")
                            try:
                                diff_result = await session.call_tool(
                                    "pull_request_read",
                                    self._read_arguments(owner, repo, pull_number, "get_diff"),
                                )
                            except Exception as exc:
                                failure = RuntimeError(f"MCP get_diff tool failure: {self._exception_detail(exc)}")
                            else:
                                self._log_diff_result_shape(diff_result)
                                if self._result_error(diff_result):
                                    failure = RuntimeError("MCP get_diff tool failure: pull_request_read returned a tool error.")
                                else:
                                    try:
                                        print("[MCP] Extracting unified diff text")
                                        diff_text, source = self._extract_diff_text(diff_result)
                                        print("[MCP] Unified diff text source:")
                                        print(source)
                                        print("[MCP] Unified diff starts with diff header:")
                                        print("true" if diff_text.lstrip().startswith("diff --git ") else "false")
                                    except Exception as exc:
                                        failure = RuntimeError(f"MCP get_diff parse failure: {self._exception_detail(exc)}")
                                    else:
                                        print("[MCP] Unified PR diff received")
                                        try:
                                            print("[MCP] Normalizing unified diff into per-file patches")
                                            self._log_diff_normalization_diagnostics(diff_text, files)
                                            normalized = self._normalize_diff_files(diff_text, files)
                                            if not normalized:
                                                raise RuntimeError("Normalization produced no file entries.")
                                        except Exception as exc:
                                            stage = "MCP diff normalization"
                                            print(f"[MCP] {stage} failed")
                                            print(f"[MCP] Exception type: {type(exc).__name__}")
                                            print(f"[MCP] Exception message: {exc}")
                                            failure = RuntimeError(
                                                f"MCP diff normalization failure: {self._exception_detail(exc)}"
                                            )

                    if failure is None and normalized is not None:
                        if not normalized:
                            print("[MCP] GitHub changed-file READ completed")
                        elif not files or not all(item["patch"].strip() for item in files):
                            print("[MCP] Diff normalized into:")
                            print(f"{len(normalized)} changed files")
                            print("[MCP] GitHub changed-file READ completed")
                        else:
                            print("[MCP] GitHub changed-file READ completed")
        except Exception as exc:
            if failure is not None:
                failure = RuntimeError(f"{failure}; MCP session shutdown also failed: {self._exception_detail(exc)}")
            else:
                failure = RuntimeError(f"MCP connection failure while reading Pull Request files: {self._exception_detail(exc)}")

        if failure is not None:
            raise failure
        if normalized is None:
            raise RuntimeError("MCP read completed without returning changed-file data.")
        return normalized

    @staticmethod
    def _write_arguments(method: str, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        return {
            "method": method,
            "owner": owner,
            "repo": repo,
            "pullNumber": pull_number,
        }

    @staticmethod
    async def _call_write_tool(session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            method = arguments.get("method", tool_name)
            raise RuntimeError(f"MCP {tool_name} ({method}) call failed: {exc}") from exc
        if GitHubMCPProvider._result_error(result):
            method = arguments.get("method", tool_name)
            raise RuntimeError(f"MCP {tool_name} ({method}) returned a tool error.")
        return result

    async def post_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        review_payload: dict[str, Any],
    ) -> None:
        """Publish an existing review payload as one MCP pending Pull Request review."""
        if not self.token:
            raise RuntimeError("Missing GITHUB_TOKEN for GitHub MCP review publishing.")

        comments = review_payload.get("comments") or []
        if not isinstance(comments, list):
            raise RuntimeError("Review payload comments must be a list.")

        print("[MCP-WRITE] Publishing Pull Request review")
        print("[MCP-WRITE] Tool:")
        print("pull_request_review_write")
        params = self.build_server_parameters()
        try:
            transport = stdio_client(params)
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    try:
                        await session.initialize()
                    except Exception as exc:
                        raise RuntimeError(f"MCP initialization failed before review publishing: {exc}") from exc

                    create_arguments = self._write_arguments("create", owner, repo, pull_number)
                    commit_id = review_payload.get("commit_id")
                    if commit_id:
                        create_arguments["commitID"] = commit_id
                    print("[MCP-WRITE] Creating pending review")
                    print("[MCP-WRITE] Commit:")
                    print("PR head SHA supplied" if commit_id else "PR head SHA not supplied")
                    await self._call_write_tool(session, "pull_request_review_write", create_arguments)
                    print("[MCP-WRITE] Pending review created")
                    print(f"[MCP-WRITE] Adding {len(comments)} validated inline comments")

                    try:
                        for comment in comments:
                            if not isinstance(comment, dict):
                                raise RuntimeError("Review payload contained a malformed inline comment.")
                            path = comment.get("path")
                            line = comment.get("line")
                            body = comment.get("body")
                            if not isinstance(path, str) or not path or not isinstance(line, int) or not isinstance(body, str):
                                raise RuntimeError("Review payload contained an incomplete inline comment.")
                            print("[MCP-WRITE] Adding inline comment:")
                            print(path)
                            print(f"RIGHT line {line}")
                            await self._call_write_tool(
                                session,
                                "add_comment_to_pending_review",
                                {
                                    "owner": owner,
                                    "repo": repo,
                                    "pullNumber": pull_number,
                                    "path": path,
                                    "body": body,
                                    "line": line,
                                    "side": "RIGHT",
                                    "subjectType": "LINE",
                                },
                            )

                        submit_arguments = self._write_arguments("submit_pending", owner, repo, pull_number)
                        submit_arguments.update({
                            "body": review_payload.get("body", ""),
                            "event": "COMMENT",
                        })
                        print("[MCP-WRITE] Submitting pending review")
                        print("[MCP-WRITE] Event:")
                        print("COMMENT")
                        print("[MCP-WRITE] Enterprise summary included")
                        await self._call_write_tool(session, "pull_request_review_write", submit_arguments)
                    except Exception as exc:
                        print("[MCP] Review publishing failed")
                        print("[MCP] Attempting pending review cleanup")
                        cleanup_arguments = self._write_arguments("delete_pending", owner, repo, pull_number)
                        try:
                            await self._call_write_tool(session, "pull_request_review_write", cleanup_arguments)
                        except Exception as cleanup_exc:
                            print("[MCP] Pending review cleanup also failed")
                            raise RuntimeError(
                                f"MCP review publishing failed: {exc}; pending review cleanup also failed: {cleanup_exc}"
                            ) from exc
                        print("[MCP] Pending review cleanup succeeded")
                        raise RuntimeError(f"MCP review publishing failed: {exc}") from exc

                    print("[MCP-WRITE] Review submitted successfully")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"MCP connection failed while publishing Pull Request review: {exc}") from exc

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
