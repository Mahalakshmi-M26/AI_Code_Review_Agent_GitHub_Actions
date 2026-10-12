from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from reviewer.finding_lifecycle import (
    compare_findings,
    decode_lifecycle_state,
    encode_lifecycle_state,
    normalize_file_path,
)
from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.review import extract_finding_id_marker


class GitHubMCPFindingLifecycle:
    """Persist finding identity and reconcile review threads using GitHub MCP."""

    PAGE_SIZE = 100
    STATE_AUTHOR = "github-actions[bot]"

    def __init__(self, provider: GitHubMCPProvider) -> None:
        self.provider = provider

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        transport = stdio_client(self.provider.build_server_parameters())
        async with transport as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def _call(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await session.call_tool(tool_name, arguments)
        if self.provider._result_error(result):
            method = arguments.get("method", tool_name)
            raise RuntimeError(f"MCP {tool_name} ({method}) returned a tool error.")
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

            if (
                isinstance(body, str)
                and extract_finding_id_marker(body) == expected_finding_id
                and isinstance(path, str)
                and normalize_file_path(path) == normalize_file_path(expected_path)
                and author == cls.STATE_AUTHOR
            ):
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
        for comment in reversed(comments):
            user = comment.get("user") or comment.get("author") or {}
            login = user.get("login") if isinstance(user, dict) else None
            body = comment.get("body")
            if login != cls.STATE_AUTHOR or not isinstance(body, str):
                continue
            records = decode_lifecycle_state(body, repository, pull_number)
            if records is None:
                continue
            try:
                return int(comment["id"]), records
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("The MCP lifecycle state comment did not include a numeric comment ID.")
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
            comments = await self._read_pr_comments(session, owner, repo, pull_number)
            state_comment_id, previous_records = self._find_state_comment(comments, repository, pull_number)
            threads = await self._read_threads(session, owner, repo, pull_number)
            used_thread_ids: set[str] = set()
            threads_by_id = {
                thread_id: thread
                for thread in threads
                if (thread_id := self._thread_id(thread))
            }

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

            comparison = compare_findings(
                list(previous_by_id.values()),
                current,
                repository,
                pull_number,
                head_sha,
                reviewed_files,
                resolve_unreviewed,
            )
            records_by_id = {str(record["finding_id"]): record for record in comparison["records"]}

            print(f"[P3] Previous findings: {len(previous_records)}")
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
                    await self._set_thread_resolution(session, owner, repo, pull_number, str(thread_id), True)

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
                        f"[P3] Thread mapped: "
                        f"{finding.get('finding_id')} -> {thread_id}"
                    )
                    print(
                        f"[P3] Thread associated "
                        f"{finding['finding_id']} -> {thread_id}"
                    )
                    print(
                        f"[P3] Thread mapped: "
                        f"{finding.get('finding_id')} -> {thread_id}"
                    )
                    used_ids.add(str(thread_id))

            state_comment_id = comparison.get("state_comment_id")
            print("[P3] Saving lifecycle state")

            for record in records:
                print(
                    f"[P3] State: "
                    f"{record.get('finding_id')} "
                    f"thread={record.get('thread_id')}"
                )
            await self._save_state(session, owner, repo, pull_number, state_comment_id, records)
            comparison["records"] = records
            return comparison