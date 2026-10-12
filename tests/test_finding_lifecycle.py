import asyncio
import json

import pytest
from mcp.types import CallToolResult, TextContent

from reviewer.finding_lifecycle import (
    append_lifecycle_summary,
    build_finding_records,
    compare_findings,
    decode_lifecycle_state,
    encode_lifecycle_state,
    finding_fingerprint,
)
from reviewer.models import ReviewResult
from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.providers.github_mcp_lifecycle import GitHubMCPFindingLifecycle


def install_fake_lifecycle_mcp(monkeypatch, responder, calls):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return {"serverInfo": {"name": "github-mcp-server", "version": "2.0.1"}}

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return responder(name, arguments)

    class FakeTransport:
        async def __aenter__(self):
            return object(), object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    import reviewer.providers.github_mcp_lifecycle as lifecycle_module

    monkeypatch.setattr(lifecycle_module, "stdio_client", lambda params: FakeTransport())
    monkeypatch.setattr(lifecycle_module, "ClientSession", FakeSession)


def test_lifecycle_session_logs_nested_exception_groups(monkeypatch, capsys):
    import reviewer.providers.github_mcp_lifecycle as lifecycle_module

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return None

    class FailingTransport:
        async def __aenter__(self):
            return object(), object()

        async def __aexit__(self, exc_type, exc, tb):
            raise ExceptionGroup("MCP task group", [
                ValueError("GitHub API rejected the request"),
                ExceptionGroup("transport shutdown", [RuntimeError("stdio closed unexpectedly")]),
            ])

    monkeypatch.setattr(lifecycle_module, "stdio_client", lambda params: FailingTransport())
    monkeypatch.setattr(lifecycle_module, "ClientSession", FakeSession)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    async def run_session():
        async with manager._session():
            pass

    with pytest.raises(ExceptionGroup, match="MCP task group"):
        asyncio.run(run_session())

    output = capsys.readouterr()
    logged = output.out + output.err
    assert "[DEBUG] ExceptionGroup detected" in logged
    assert "ValueError" in logged
    assert "GitHub API rejected the request" in logged
    assert "RuntimeError" in logged
    assert "stdio closed unexpectedly" in logged
    assert "Traceback (most recent call last)" in logged


def mcp_payload(payload):
    return CallToolResult(content=[], structured_content=payload, is_error=False)


def mcp_json_text(payload):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))], is_error=False)


def finding(title: str, line: int, code_context: str) -> dict:
    return {
        "file": "src/app.py",
        "category": "Security",
        "severity": "HIGH",
        "title": title,
        "line": line,
        "code_context": code_context,
    }


def record(finding_id: str, title: str, status: str = "open") -> dict:
    return {
        "finding_id": finding_id,
        "repository": "octo/demo",
        "pr_number": 12,
        "head_sha": "old-head",
        "status": status,
        "thread_id": f"thread-{title}",
        "file": "src/app.py",
        "title": title,
    }


def state_comment(comment_id: int, records: list[dict]) -> dict:
    return {
        "id": comment_id,
        "body": encode_lifecycle_state("octo/demo", 12, records),
        "user": {"login": "github-actions[bot]"},
    }


def review_thread(thread_id: str, body: str, resolved: bool = False) -> dict:
    return {
        "id": thread_id,
        "is_resolved": resolved,
        "comments": [{"body": body, "path": "src/app.py", "author": "github-actions[bot]"}],
    }


def test_fingerprint_is_stable_across_line_shifts_and_normalization():
    earlier = finding("Validate owner", 10, "if owner_id is None:\n    raise ValueError()")
    later = finding(" validate   OWNER ", 37, " if owner_id is None:  raise ValueError() ")

    assert finding_fingerprint(earlier, earlier["code_context"]) == finding_fingerprint(
        later,
        later["code_context"],
    )


def test_title_wording_changes_keep_finding_existing():
    first_review = finding(
        "Top-level executable code causes side effects on import",
        8,
        "print('initialized')",
    )
    second_review = finding(
        "Module-level executable code causes side effects on import",
        8,
        "print('initialized')",
    )
    first_id = finding_fingerprint(first_review, first_review["code_context"])
    second_id = finding_fingerprint(second_review, second_review["code_context"])

    compared = compare_findings(
        [{**record(first_id, first_review["title"]), "code_context": first_review["code_context"]}],
        [{**record(second_id, second_review["title"]), "code_context": second_review["code_context"]}],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )

    assert first_id == second_id
    assert len(compared["existing_findings"]) == 1
    assert compared["resolved_findings"] == []
    assert compared["new_findings"] == []


def test_comparison_classifies_new_existing_and_resolved_findings():
    previous = [record("A", "A"), record("B", "B"), record("C", "C")]
    current = [
        {**record("B", "B"), "head_sha": "new-head"},
        {**record("C", "C"), "head_sha": "new-head"},
        {**record("D", "D"), "head_sha": "new-head", "thread_id": None},
    ]

    compared = compare_findings(
        previous,
        current,
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )

    assert [item["finding_id"] for item in compared["new_findings"]] == ["D"]
    assert [item["finding_id"] for item in compared["existing_findings"]] == ["B", "C"]
    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert compared["resolved_findings"][0]["status"] == "resolved"
    records_by_id = {item["finding_id"]: item for item in compared["records"]}
    assert records_by_id["B"]["thread_id"] == "thread-B"


def test_unreviewed_file_does_not_resolve_finding():
    compared = compare_findings(
        [record("A", "A")],
        [],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/other.py"],
    )

    assert compared["resolved_findings"] == []
    assert compared["records"][0]["status"] == "open"


def test_resolved_finding_returning_is_existing_and_reopened():
    current = {**record("A", "A"), "thread_id": "thread-A"}
    compared = compare_findings(
        [record("A", "A", status="resolved")],
        [current],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )

    assert [item["finding_id"] for item in compared["existing_findings"]] == ["A"]
    assert [item["finding_id"] for item in compared["reopened_findings"]] == ["A"]
    assert compared["records"][0]["status"] == "open"


def test_state_round_trip_is_scoped_to_repository_and_pull_request():
    records = [record("A", "A")]
    body = encode_lifecycle_state("octo/demo", 12, records)

    assert decode_lifecycle_state(body, "octo/demo", 12) == records
    assert decode_lifecycle_state(body, "octo/other", 12) is None
    assert decode_lifecycle_state(body, "octo/demo", 13) is None


def test_lifecycle_summary_lists_all_three_states():
    body = append_lifecycle_summary(
        "Existing review summary",
        {
            "resolved_findings": [{"title": "Owner validation", "file": "src/app.py"}],
            "new_findings": [{"title": "Pagination", "file": "src/api.py"}],
            "existing_findings": [{"title": "DTO usage", "file": "src/model.py"}],
        },
    )

    assert "Existing review summary" in body
    assert "## Finding Lifecycle" in body
    assert "### Resolved Since Last Review" in body
    assert "### New Findings" in body
    assert "### Existing Findings" in body
    assert "Owner validation" in body
    assert "Pagination" in body
    assert "DTO usage" in body


def test_build_finding_records_uses_source_context_stable_across_line_shifts(tmp_path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("header = 1\nif owner_id is None:\n    raise ValueError()\n", encoding="utf-8")
    result = ReviewResult.model_validate({
        "decision": "ADVISORY",
        "risk_level": "HIGH",
        "findings": [{
            "severity": "HIGH",
            "category": "Security",
            "file": "src/app.py",
            "line": 2,
            "title": "Validate owner",
            "issue": "The owner identifier is missing.",
            "recommendation": "Validate the owner.",
        }],
        "summary": "One finding.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security"],
    })

    earlier = build_finding_records(result, {}, str(tmp_path))[0]
    source.write_text("new_header = True\nheader = 1\nif owner_id is None:\n    raise ValueError()\n", encoding="utf-8")
    shifted_result = result.model_copy(update={
        "findings": [result.findings[0].model_copy(update={"line": 3})],
    })
    later = build_finding_records(shifted_result, {}, str(tmp_path))[0]

    assert earlier["finding_id"] == later["finding_id"]
    assert earlier["line"] == 2
    assert later["line"] == 3


def test_reconcile_resolves_only_missing_finding_thread(monkeypatch):
    calls = []
    previous = [record("A", "A"), record("B", "B")]
    comments = [state_comment(91, previous)]
    threads = [review_thread("thread-A", "old A"), review_thread("thread-B", "old B")]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    provider = GitHubMCPProvider(token="test-token")
    manager = GitHubMCPFindingLifecycle(provider)
    current = [{**record("B", "B"), "thread_id": None}, {**record("D", "D"), "thread_id": None}]

    compared = asyncio.run(manager.reconcile(
        "octo", "demo", 12, "new-head", current, ["src/app.py"],
    ))

    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert [item["finding_id"] for item in compared["existing_findings"]] == ["B"]
    assert [item["finding_id"] for item in compared["new_findings"]] == ["D"]
    assert [(name, args["method"]) for name, args in calls if name == "pull_request_review_write"] == [
        ("pull_request_review_write", "resolve_thread"),
    ]
    assert not any(name in {"add_issue_comment", "update_issue_comment"} for name, _ in calls)


def test_reconcile_backfills_legacy_thread_before_resolving(monkeypatch, capsys):
    calls = []
    finding_id = "legacy-finding"
    thread_id = "PRRT_legacy-thread"
    previous = [{
        **record(finding_id, "Old finding"),
        "thread_id": None,
        "inline_body": "Original comment wording",
        "status": "open",
    }]
    comments = [state_comment(91, previous)]
    threads = [review_thread(
        thread_id,
        "Edited comment wording\n\n<!-- ai-review-finding-id:legacy-finding -->",
    )]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))
    compared = asyncio.run(manager.reconcile(
        "octo", "demo", 12, "new-head", [], ["src/app.py"],
    ))

    assert [item["finding_id"] for item in compared["resolved_findings"]] == [finding_id]
    assert compared["resolved_findings"][0]["thread_id"] == thread_id
    assert compared["records"][0]["thread_id"] == thread_id
    assert any(
        name == "pull_request_review_write"
        and arguments.get("method") == "resolve_thread"
        and arguments.get("threadId") == thread_id
        for name, arguments in calls
    )
    asyncio.run(manager.finalize("octo", "demo", 12, compared))
    assert any(
        name == "update_issue_comment"
        and f'"thread_id":"{thread_id}"' in arguments["body"]
        for name, arguments in calls
    )
    output = capsys.readouterr().out
    assert f"[P3] BACKFILLED THREAD finding={finding_id} thread={thread_id}" in output
    assert f"[P3] Resolving finding {finding_id} thread={thread_id}" in output
    assert f"[P3] MCP resolve_thread {thread_id}" in output


def test_reconcile_reopens_returning_resolved_finding(monkeypatch):
    calls = []
    previous = [record("A", "A", status="resolved")]
    comments = [state_comment(91, previous)]
    threads = [review_thread("thread-A", "old A", resolved=True)]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    compared = asyncio.run(GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token")).reconcile(
        "octo", "demo", 12, "new-head", [{**record("A", "A"), "thread_id": None}], ["src/app.py"],
    ))

    assert [item["finding_id"] for item in compared["reopened_findings"]] == ["A"]
    assert [(name, args["method"]) for name, args in calls if name == "pull_request_review_write"] == [
        ("pull_request_review_write", "unresolve_thread"),
    ]


def test_existing_thread_without_saved_state_suppresses_duplicate(monkeypatch):
    calls = []
    current = {**record("A", "A"), "thread_id": None, "inline_body": "original review body"}
    threads = [review_thread(
        "thread-existing",
        "wording changed on rerun\n\n<!-- ai-review-finding-id:A -->",
    )]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload([])
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"id": "101"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    compared = asyncio.run(GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token")).reconcile(
        "octo", "demo", 12, "new-head", [current], ["src/app.py"],
    ))

    assert compared["new_findings"] == []
    assert [item["finding_id"] for item in compared["existing_findings"]] == ["A"]
    assert compared["records"][0]["thread_id"] == "thread-existing"
    assert not any(name == "pull_request_review_write" for name, _ in calls)
    assert not any(name in {"add_issue_comment", "update_issue_comment"} for name, _ in calls)


def test_open_finding_without_thread_is_retried_as_new(monkeypatch):
    calls = []
    comments = [state_comment(91, [{**record("A", "A"), "thread_id": None}])]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": [], "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    current = {**record("A", "A"), "thread_id": None, "inline_body": "not yet published"}
    compared = asyncio.run(GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token")).reconcile(
        "octo", "demo", 12, "new-head", [current], ["src/app.py"],
    ))

    assert [item["finding_id"] for item in compared["new_findings"]] == ["A"]
    assert compared["existing_findings"] == []
    assert not any(name in {"add_issue_comment", "update_issue_comment"} for name, _ in calls)


def test_finalize_persists_new_thread_id(monkeypatch):
    calls = []
    threads = [review_thread(
        "thread-new",
        "new review body\n\n<!-- ai-review-finding-id:D -->",
    )]

    def responder(name, arguments):
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"id": "91"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))
    compared = {
        "records": [{**record("D", "D"), "thread_id": None, "inline_body": "new review body"}],
        "state_comment_id": 91,
        "thread_ids_before": set(),
    }

    finalized = asyncio.run(manager.finalize("octo", "demo", 12, compared))

    assert finalized["records"][0]["thread_id"] == "thread-new"
    assert calls[-1][0] == "update_issue_comment"
    saved = json.loads(calls[-1][1]["body"].split("\n", 1)[1].rsplit("\n-->", 1)[0])
    assert saved["findings"][0]["thread_id"] == "thread-new"


def test_finalize_matches_mcp_author_and_resolves_thread(monkeypatch, capsys):
    calls = []
    saved_records = []
    finding_id = "f64d466c6acb9fd760d2eb11ddcab34f4b937e13e7bc92f0df899e6d98e9116c"
    thread_id = "PRRT_kwDOUUd-4M6rfsAA"
    threads = [{
        "id": thread_id,
        "isResolved": False,
        "comments": {
            "nodes": [{
                "body": f"MCP changed this comment text\n\n<!-- ai-review-finding-id:{finding_id} -->",
                "path": "./src/app.py",
                "author": "github-actions",
            }],
        },
    }]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload([state_comment(91, saved_records)])
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"id": "91"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))
    compared = {
        "records": [{
            **record(finding_id, "D"),
            "thread_id": None,
            "inline_body": "new review body",
        }],
        "state_comment_id": 91,
        "thread_ids_before": set(),
    }

    finalized = asyncio.run(manager.finalize("octo", "demo", 12, compared))

    assert finalized["records"][0]["thread_id"] == thread_id
    saved = json.loads(calls[-1][1]["body"].split("\n", 1)[1].rsplit("\n-->", 1)[0])
    assert saved["findings"][0]["finding_id"] == finding_id
    assert saved["findings"][0]["thread_id"] == thread_id
    output = capsys.readouterr().out
    assert f"[P3] EXTRACTED_MARKER={finding_id}" in output
    assert f"[P3] EXPECTED_FINDING_ID={finding_id}" in output
    assert "[P3] AUTHOR=github-actions" in output
    assert "[P3] AUTHOR_MATCH=True" in output
    assert "[P3] PATH_MATCH=True" in output
    assert f"[P3] MATCH FOUND finding={finding_id} thread={thread_id}" in output
    assert f"[P3] SAVING THREAD finding={finding_id} thread={thread_id}" in output
    saved_records.extend(saved["findings"])

    second_review = asyncio.run(manager.reconcile(
        "octo", "demo", 12, "next-head", [], ["src/app.py"],
    ))

    assert [item["finding_id"] for item in second_review["resolved_findings"]] == [finding_id]
    assert second_review["resolved_findings"][0]["thread_id"] == thread_id
    assert any(
        name == "pull_request_review_write"
        and arguments.get("method") == "resolve_thread"
        and arguments.get("threadId") == thread_id
        for name, arguments in calls
    )