from __future__ import annotations

from contextlib import asynccontextmanager
import json
import traceback
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from reviewer.finding_lifecycle import (
    compare_findings,
    decode_lifecycle_state,
    encode_lifecycle_state,
    normalize_file_path,
    STATE_END_MARKER,
    STATE_MARKER,
)
from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.review import extract_finding_id_marker


class MCPToolError(RuntimeError):
    """An MCP tool returned an error result."""


class GitHubMCPFindingLifecycle:
    """Persist finding identity and reconcile review threads using GitHub MCP."""

    PAGE_SIZE = 100
    STATE_AUTHOR = "github-actions[bot]"
    STATE_COMMENT_AUTHORS = {STATE_AUTHOR, "github-actions", "Mahalakshmi-M26"}
    REVIEW_COMMENT_AUTHORS = {"github-actions", STATE_AUTHOR, "Mahalakshmi-M26"}

    def __init__(self, provider: GitHubMCPProvider) -> None:
        self.provider = provider

    @staticmethod
    def _log_exception_group(exception_group: BaseExceptionGroup[Any], depth: int = 0) -> None:
        indent = "  " * depth
        for index, exception in enumerate(exception_group.exceptions):
            print(f"[DEBUG] {indent}Sub-exception #{index}")
            print(f"[DEBUG] {indent}Type: {type(exception).__name__}")
            print(f"[DEBUG] {indent}Message: {exception}")
            traceback.print_exception(exception)
            if isinstance(exception, BaseExceptionGroup):
                print(f"[DEBUG] {indent}Nested exception group contents:")
                GitHubMCPFindingLifecycle._log_exception_group(exception, depth + 1)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        transport = stdio_client(self.provider.build_server_parameters())
        try:
            async with transport as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except* Exception as exception_group:
            print("[DEBUG] ExceptionGroup detected while running GitHub MCP lifecycle operation")
            print(f"[DEBUG] ExceptionGroup type: {type(exception_group).__name__}")
            print(f"[DEBUG] ExceptionGroup message: {exception_group}")
            traceback.print_exception(exception_group)
            self._log_exception_group(exception_group)
            raise

    async def _call(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        is_resolve_thread = (
            tool_name == "pull_request_review_write"
            and arguments.get("method") == "resolve_thread"
        )
        if is_resolve_thread:
            print("[MCP REQUEST] Tool: pull_request_review_write")
            print(f"[MCP REQUEST] Arguments: {arguments!r}")
            print(
                f"[MCP REQUEST] owner={arguments.get('owner')!r} "
                f"repo={arguments.get('repo')!r} "
                f"pull_number={arguments.get('pullNumber')!r} "
                f"thread_id={arguments.get('threadId')!r}"
            )
            print(f"[MCP REQUEST] Payload structure: {arguments!r}")
            try:
                tools_response = await session.list_tools()
                raw_tools = getattr(tools_response, "tools", None)
                if raw_tools is None and isinstance(tools_response, dict):
                    raw_tools = tools_response.get("tools", [])
                for tool in raw_tools or []:
                    normalized = self.provider._normalize_tool(tool)
                    if normalized.get("name") == tool_name:
                        print("[MCP SCHEMA] pull_request_review_write inputSchema:")
                        print(repr(normalized.get("inputSchema") or normalized.get("input_schema")))
                        break
                else:
                    print("[MCP SCHEMA] pull_request_review_write not found in list_tools response")
            except Exception as schema_error:
                print("[MCP SCHEMA] Could not inspect pull_request_review_write schema:")
                print(f"{type(schema_error).__name__}: {schema_error}")

        result = await session.call_tool(tool_name, arguments)
        result_is_error = self.provider._result_error(result)
        if is_resolve_thread:
            print("[MCP RESPONSE] Raw result:")
            print(result)
            print("[MCP RESPONSE] repr(result):")
            print(repr(result))
            print(f"[MCP RESPONSE] _result_error(result)={result_is_error!r}")
            try:
                content = self.provider._result_content(result)
                print("[MCP RESPONSE] Result content:")
                print(content)
                try:
                    decoded = self.provider._decode_json_payload(content, "resolve_thread")
                    print("[MCP RESPONSE] Decoded payload:")
                    print(repr(decoded))
                except Exception as decode_error:
                    print(f"[MCP RESPONSE] Payload decode failed: {type(decode_error).__name__}: {decode_error}")
            except Exception as content_error:
                print(f"[MCP RESPONSE] Content extraction failed: {type(content_error).__name__}: {content_error}")

        if result_is_error:
            method = arguments.get("method", tool_name)
            print("[MCP ERROR] Tool response:")
            print(repr(result))
            error_detail: Any = None
            try:
                content = self.provider._result_content(result)
                error_detail = content
                print("[MCP ERROR] Result content:")
                print(content)
                print("[MCP ERROR] Decoded payload:")
                try:
                    print(self.provider._decode_json_payload(content, str(method)))
                except Exception as decode_error:
                    print(f"[MCP ERROR] Could not decode payload: {type(decode_error).__name__}: {decode_error}")
            except Exception as content_error:
                print("[MCP ERROR] Result content unavailable:")
                print(f"{type(content_error).__name__}: {content_error}")
            detail = f": {error_detail}" if error_detail is not None else "."
            raise MCPToolError(f"MCP {tool_name} ({method}) returned a tool error{detail}")
        return result

    def _payload(self, result: Any, label: str) -> Any:
        return self.provider._decode_json_payload(self.provider._result_content(result), label)

    async def _read_threads(
        self,
        session: ClientSession,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "method": "get_review_comments",
                "owner": owner,
                "repo": repo,
                "pullNumber": pull_number,
                "perPage": self.PAGE_SIZE,
            }
            if cursor:
                arguments["after"] = cursor
            result = await self._call(session, "pull_request_read", arguments)
            payload = self._payload(result, "get_review_comments")
            if not isinstance(payload, dict):
                raise RuntimeError("MCP get_review_comments response did not contain thread metadata.")
            page_threads = payload.get("review_threads", payload.get("reviewThreads", []))
            if not isinstance(page_threads, list):
                raise RuntimeError("MCP get_review_comments response did not contain a review_threads list.")
            threads.extend(item for item in page_threads if isinstance(item, dict))
            page_info = payload.get("pageInfo", payload.get("page_info", {}))
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage", page_info.get("has_next_page")):
                break
            cursor = page_info.get("endCursor", page_info.get("end_cursor"))
            if not isinstance(cursor, str) or not cursor:
                raise RuntimeError("MCP review-thread pagination requested another page without an end cursor.")
        return threads

    async def _read_pr_comments(
        self,
        session: ClientSession,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        page = 1
        print(f"[P3] Reading PR issue comments repository={owner}/{repo} issue_number={pull_number}")
        while True:
            result = await self._call(
                session,
                "pull_request_read",
                {
                    "method": "get_comments",
                    "owner": owner,
                    "repo": repo,
                    "pullNumber": pull_number,
                    "page": page,
                    "perPage": self.PAGE_SIZE,
                },
            )
            payload = self._payload(result, "get_comments")
            if isinstance(payload, dict):
                payload = payload.get("comments", [])
            if not isinstance(payload, list):
                raise RuntimeError("MCP get_comments response did not contain a comments list.")
            print(f"[P3] PR issue comment page={page} returned={len(payload)}")
            comments.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < self.PAGE_SIZE:
                break
            page += 1
        return comments

    @staticmethod
    def _thread_id(thread: dict[str, Any]) -> str | None:
        value = thread.get("id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _thread_resolved(thread: dict[str, Any]) -> bool:
        return bool(thread.get("is_resolved", thread.get("isResolved", False)))

    @staticmethod
    def _thread_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
        comments = thread.get("comments", [])
        if isinstance(comments, dict):
            comments = comments.get("nodes", comments.get("edges", []))
        if not isinstance(comments, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in comments:
            if not isinstance(item, dict):
                continue
            node = item.get("node", item)
            if isinstance(node, dict):
                normalized.append(node)
        return normalized

    @staticmethod
    def _comment_author(comment: dict[str, Any]) -> str | None:
        author = comment.get("author", comment.get("user"))
        if isinstance(author, dict):
            author = author.get("login")
        return author if isinstance(author, str) else None

    @classmethod
    def _comment_matches(cls, thread: dict[str, Any], finding: dict[str, Any]) -> bool:
        expected_finding_id = finding.get("finding_id")
        expected_path = finding.get("file")

        print(
            f"[P3] Looking for match: "
            f"path={expected_path}"
        )

        if not isinstance(expected_finding_id, str) or not expected_finding_id:
            return False

        if not isinstance(expected_path, str):
            return False

        for comment in cls._thread_comments(thread):
            body = comment.get("body")
            path = comment.get("path")
            author = cls._comment_author(comment)
            extracted_marker = extract_finding_id_marker(body) if isinstance(body, str) else None
            path_matches = (
                isinstance(path, str)
                and normalize_file_path(path) == normalize_file_path(expected_path)
            )
            author_matches = author in cls.REVIEW_COMMENT_AUTHORS
            print(f"[P3] EXTRACTED_MARKER={extracted_marker}")
            print(f"[P3] EXPECTED_FINDING_ID={expected_finding_id}")
            print(f"[P3] AUTHOR={author}")
            print(f"[P3] AUTHOR_MATCH={author_matches}")
            print(f"[P3] PATH_MATCH={path_matches}")

            if (
                extracted_marker == expected_finding_id
                and path_matches
                and author_matches
            ):
                print(
                    f"[P3] MATCH FOUND finding={expected_finding_id} "
                    f"thread={thread.get('id')}"
                )
                return True
        return False

    @classmethod
    def _find_matching_thread(
        cls,
        finding: dict[str, Any],
        threads: list[dict[str, Any]],
        used_thread_ids: set[str],
    ) -> dict[str, Any] | None:
        matches = [
            thread for thread in threads
            if (thread_id := cls._thread_id(thread))
            and thread_id not in used_thread_ids
            and cls._comment_matches(thread, finding)
        ]
        if not matches:
            return None
        return next((thread for thread in matches if not cls._thread_resolved(thread)), matches[0])

    @classmethod
    def _find_state_comment(
        cls,
        comments: list[dict[str, Any]],
        repository: str,
        pull_number: int,
    ) -> tuple[int | None, list[dict[str, Any]]]:
        print(
            f"[P3] Inspecting {len(comments)} PR issue comments for lifecycle state "
            f"repository={repository} pull_number={pull_number} "
            f"accepted_authors={sorted(cls.STATE_COMMENT_AUTHORS)!r}"
        )
        selected: tuple[int, list[dict[str, Any]]] | None = None
        for comment in reversed(comments):
            user = comment.get("user") or comment.get("author") or {}
            login = user.get("login") if isinstance(user, dict) else None
            body = comment.get("body")
            comment_id = comment.get("id")
            body_preview = body[:100] if isinstance(body, str) else repr(body)[:100]
            contains_marker = isinstance(body, str) and STATE_MARKER.strip() in body
            print(
                f"[P3] State comment candidate id={comment_id!r} author={login!r} "
                f"body_preview={body_preview!r} contains_lifecycle_marker={contains_marker}"
            )

            rejection_reason: str | None = None
            if login not in cls.STATE_COMMENT_AUTHORS:
                rejection_reason = f"author {login!r} is not in the accepted author list"
            elif not isinstance(body, str):
                rejection_reason = "comment body is not a string"
            elif not body.startswith(STATE_MARKER) or not body.endswith(STATE_END_MARKER):
                rejection_reason = "lifecycle marker framing is missing or altered"

            payload: Any = None
            if rejection_reason is None:
                encoded = body[len(STATE_MARKER):-len(STATE_END_MARKER)]
                try:
                    payload = json.loads(encoded)
                except json.JSONDecodeError:
                    rejection_reason = "lifecycle marker payload is not valid JSON"

            if rejection_reason is None and not isinstance(payload, dict):
                rejection_reason = "lifecycle payload is not a JSON object"
            if rejection_reason is None and payload.get("version") != 1:
                rejection_reason = f"unsupported lifecycle state version {payload.get('version')!r}"
            if rejection_reason is None and payload.get("repository") != repository:
                rejection_reason = (
                    f"repository mismatch: stored={payload.get('repository')!r} "
                    f"requested={repository!r}"
                )
            if rejection_reason is None and payload.get("pr_number") != pull_number:
                rejection_reason = (
                    f"PR number mismatch: stored={payload.get('pr_number')!r} "
                    f"requested={pull_number!r}"
                )
            if rejection_reason is None and not isinstance(payload.get("findings"), list):
                rejection_reason = "lifecycle payload findings field is not a list"
            if rejection_reason is None:
                records = decode_lifecycle_state(body, repository, pull_number)
                if records is None:
                    rejection_reason = "lifecycle payload failed state decoding"
            if rejection_reason is None:
                try:
                    numeric_comment_id = int(comment_id)
                except (TypeError, ValueError):
                    rejection_reason = "comment ID is missing or non-numeric"
                else:
                    if selected is None:
                        selected = (numeric_comment_id, records)
                        selection = "selected as newest valid lifecycle state"
                    else:
                        selection = "valid lifecycle state, but newer valid state already selected"
                    print(
                        f"[P3] State comment id={comment_id!r} ACCEPTED: {selection}; "
                        f"finding_count={len(records)} "
                        f"finding_ids={[str(record['finding_id']) for record in records]}"
                    )
                    continue

            print(f"[P3] State comment id={comment_id!r} REJECTED: {rejection_reason}")

        if selected is not None:
            return selected
        return None, []

    async def _save_state(
        self,
        session: ClientSession,
        owner: str,
        repo: str,
        pull_number: int,
        state_comment_id: int | None,
        records: list[dict[str, Any]],
    ) -> int:
        body = encode_lifecycle_state(f"{owner}/{repo}", pull_number, records)
        if state_comment_id is not None:
            await self._call(
                session,
                "update_issue_comment",
                {"owner": owner, "repo": repo, "comment_id": state_comment_id, "body": body},
            )
            return state_comment_id

        result = await self._call(
            session,
            "add_issue_comment",
            {"owner": owner, "repo": repo, "issue_number": pull_number, "body": body},
        )
        payload = self._payload(result, "add_issue_comment")
        if isinstance(payload, dict):
            candidate = payload.get("id")
            if candidate is None and isinstance(payload.get("single"), dict):
                candidate = payload["single"].get("id")
            if candidate is not None:
                try:
                    return int(candidate)
                except (TypeError, ValueError):
                    pass
        comments = await self._read_pr_comments(session, owner, repo, pull_number)
        created_id, _ = self._find_state_comment(comments, f"{owner}/{repo}", pull_number)
        if created_id is None:
            raise RuntimeError("MCP could not identify the persisted lifecycle state comment.")
        return created_id

    async def _set_thread_resolution(
        self,
        session: ClientSession,
        owner: str,
        repo: str,
        pull_number: int,
        thread_id: str,
        resolve: bool,
    ) -> None:
        await self._call(
            session,
            "pull_request_review_write",
            {
                "method": "resolve_thread" if resolve else "unresolve_thread",
                "owner": owner,
                "repo": repo,
                "pullNumber": pull_number,
                "threadId": thread_id,
            },
        )

    async def reconcile(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        head_sha: str,
        current_findings: list[dict[str, Any]],
        reviewed_files: list[str],
        resolve_unreviewed: bool = False,
    ) -> dict[str, Any]:
        """Read durable state, reconcile existing threads, and save this revision's findings."""
        repository = f"{owner}/{repo}"
        async with self._session() as session:
            print(f"[P3] Lifecycle state location: PR issue comment {repository}#{pull_number}")
            comments = await self._read_pr_comments(session, owner, repo, pull_number)
            state_comment_id, previous_records = self._find_state_comment(comments, repository, pull_number)
            previous_ids = [str(record["finding_id"]) for record in previous_records]
            print(
                f"[P3] Loaded lifecycle state comment id={state_comment_id} "
                f"finding_count={len(previous_records)}"
            )
            print(f"[P3] Loaded lifecycle finding IDs: {previous_ids}")
            threads = await self._read_threads(session, owner, repo, pull_number)
            used_thread_ids: set[str] = set()
            threads_by_id = {
                thread_id: thread
                for thread in threads
                if (thread_id := self._thread_id(thread))
            }

            for record in previous_records:
                if record.get("thread_id"):
                    used_thread_ids.add(str(record["thread_id"]))
                    continue
                match = self._find_matching_thread(record, threads, used_thread_ids)
                if match is None:
                    continue
                thread_id = self._thread_id(match)
                if not thread_id:
                    continue
                record["thread_id"] = thread_id
                used_thread_ids.add(thread_id)
                print(
                    f"[P3] BACKFILLED THREAD "
                    f"finding={record['finding_id']} thread={record['thread_id']}"
                )

            previous_by_id = {str(record["finding_id"]): record for record in previous_records}
            current = [dict(finding) for finding in current_findings]
            for finding in current:
                previous = previous_by_id.get(str(finding.get("finding_id")))
                if previous and previous.get("thread_id"):
                    finding["thread_id"] = previous["thread_id"]
                    used_thread_ids.add(str(previous["thread_id"]))
                    continue
                match = self._find_matching_thread(finding, threads, used_thread_ids)
                if match is None:
                    if (
                        previous
                        and previous.get("status") == "open"
                        and not previous.get("thread_id")
                        and finding.get("inline_body")
                    ):
                        previous_by_id.pop(str(finding["finding_id"]), None)
                    continue
                thread_id = self._thread_id(match)
                if not thread_id:
                    continue
                finding["thread_id"] = thread_id
                print(
                    f"[P3] Thread mappedo: "
                    f"{finding.get('finding_id')} -> {thread_id}"
                )
                used_thread_ids.add(thread_id)
                if previous is None:
                    previous_by_id[str(finding["finding_id"])] = {
                        **finding,
                        "repository": repository,
                        "pr_number": pull_number,
                        "head_sha": head_sha,
                        "status": "resolved" if self._thread_resolved(match) else "open",
                    }

            comparison_previous = list(previous_by_id.values())
            comparison_previous_ids = [str(record["finding_id"]) for record in comparison_previous]
            comparison = compare_findings(
                comparison_previous,
                current,
                repository,
                pull_number,
                head_sha,
                reviewed_files,
                resolve_unreviewed,
            )
            records_by_id = {str(record["finding_id"]): record for record in comparison["records"]}

            print(f"[P3] Previous findings: {len(comparison_previous)}")
            print(f"[P3] Previous finding IDs: {comparison_previous_ids}")
            print(f"[P3] Current findings: {len(current)}")
            print(f"[P3] Resolved findings: {len(comparison['resolved_findings'])}")

            for finding in comparison["resolved_findings"]:
                print(
                    f"[P3] Resolving finding "
                    f"{finding.get('finding_id')} "
                    f"thread={finding.get('thread_id')}"
                )
                thread_id = finding.get("thread_id")
                thread = threads_by_id.get(str(thread_id)) if thread_id else None
                if thread is not None and not self._thread_resolved(thread):
                    print(f"[P3] MCP resolve_thread {thread_id}")
                    try:
                        await self._set_thread_resolution(
                            session,
                            owner,
                            repo,
                            pull_number,
                            str(thread_id),
                            True,
                        )
                    except MCPToolError as exc:
                        print(
                            f"[P3] WARNING: Failed to resolve thread "
                            f"{thread_id}. Continuing. Error={exc}"
                        )
                        record = records_by_id.get(str(finding.get("finding_id")))
                        if record is not None:
                            record["status"] = "open"
                            record["thread_resolution_pending"] = True
                        continue

                record = records_by_id.get(str(finding.get("finding_id")))
                if record is not None:
                    record.pop("thread_resolution_pending", None)

            for finding in comparison["existing_findings"]:
                print(
                    f"[P3] Existing finding "
                    f"{finding.get('finding_id')} "
                    f"thread_id={finding.get('thread_id')}"
                )
                thread_id = finding.get("thread_id")
                thread = threads_by_id.get(str(thread_id)) if thread_id else None
                if thread is not None and self._thread_resolved(thread):
                    await self._set_thread_resolution(session, owner, repo, pull_number, str(thread_id), False)
                    if not any(item["finding_id"] == finding["finding_id"] for item in comparison["reopened_findings"]):
                        comparison["reopened_findings"].append(finding)

            comparison.update({
                "records": list(records_by_id.values()),
                "state_comment_id": state_comment_id,
                "thread_ids_before": set(threads_by_id),
            })
            return comparison

    async def finalize(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        comparison: dict[str, Any],
    ) -> dict[str, Any]:
        """Associate newly published inline comments with their MCP review-thread IDs."""
        async with self._session() as session:
            threads = await self._read_threads(session, owner, repo, pull_number)
            for thread in threads[:3]:
                print(
                    f"[P3] MCP thread.id={thread.get('id')!r} "
                    f"thread.comments={thread.get('comments')!r}"
                )
                for comment in self._thread_comments(thread):
                    print(
                        f"[P3] MCP comment.body={comment.get('body')!r} "
                        f"comment.path={comment.get('path')!r} "
                        f"comment.author={comment.get('author', comment.get('user'))!r}"
                    )
            old_ids = comparison.get("thread_ids_before") or set()
            records = [dict(record) for record in comparison.get("records", [])]
            used_ids = {str(record["thread_id"]) for record in records if record.get("thread_id")}
            for finding in records:
                if finding.get("thread_id") or not finding.get("inline_body"):
                    continue
                matches = [
                    thread for thread in threads
                    if (thread_id := self._thread_id(thread))
                    and thread_id not in old_ids
                    and thread_id not in used_ids
                    and self._comment_matches(thread, finding)
                ]
                if len(matches) == 1:
                    thread_id = self._thread_id(matches[0])
                    finding["thread_id"] = thread_id
                    print(
                        f"[P3] SAVING THREAD "
                        f"finding={finding['finding_id']} thread={thread_id}"
                    )
                    used_ids.add(str(thread_id))

            state_comment_id = comparison.get("state_comment_id")
            print("[P3] Saving lifecycle state")
            print(f"[P3] Lifecycle state location: PR issue comment {owner}/{repo}#{pull_number}")
            print(f"[P3] Saved lifecycle finding IDs: {[str(record['finding_id']) for record in records]}")

            for record in records:
                print(
                    f"[P3] State: "
                    f"{record.get('finding_id')} "
                    f"thread={record.get('thread_id')}"
                )
            saved_comment_id = await self._save_state(
                session,
                owner,
                repo,
                pull_number,
                state_comment_id,
                records,
            )
            print(f"[P3] Lifecycle state saved in PR issue comment id={saved_comment_id}")
            comparison["records"] = records
            return comparison