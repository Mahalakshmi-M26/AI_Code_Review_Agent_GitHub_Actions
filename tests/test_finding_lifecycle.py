import asyncio
import json

import pytest
from mcp.types import CallToolResult, TextContent

from reviewer.diff_parser import build_reviewed_line_scope_from_prompt_diff
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


def test_lifecycle_tool_error_logs_full_mcp_payload(capsys):
    payload = {
        "message": "GraphQL mutation failed",
        "error": "thread is already resolved",
        "details": {"threadId": "PRRT_example"},
        "type": "NOT_FOUND",
        "extensions": {"code": "NOT_FOUND"},
    }

    class ErrorSession:
        async def list_tools(self):
            return {"tools": [{
                "name": "pull_request_review_write",
                "inputSchema": {
                    "type": "object",
                    "required": ["method", "owner", "repo", "pullNumber", "threadId"],
                },
            }]}

        async def call_tool(self, name, arguments):
            return CallToolResult(content=[], structured_content=payload, is_error=True)

    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    with pytest.raises(RuntimeError, match="resolve_thread.*tool error"):
        asyncio.run(manager._call(
            ErrorSession(),
            "pull_request_review_write",
            {
                "method": "resolve_thread",
                "owner": "octo",
                "repo": "demo",
                "pullNumber": 12,
                "threadId": "PRRT_example",
            },
        ))

    output = capsys.readouterr().out
    assert "[MCP REQUEST] owner='octo' repo='demo' pull_number=12 thread_id='PRRT_example'" in output
    assert "'pullNumber': 12" in output
    assert "'threadId': 'PRRT_example'" in output
    assert "[MCP SCHEMA] pull_request_review_write inputSchema:" in output
    assert "'required': ['method', 'owner', 'repo', 'pullNumber', 'threadId']" in output
    assert "[MCP RESPONSE] repr(result):" in output
    assert "[MCP RESPONSE] _result_error(result)=True" in output
    assert "[MCP ERROR] Tool response:" in output
    assert "[MCP ERROR] Result content:" in output
    assert "[MCP ERROR] Decoded payload:" in output
    for detail in ("GraphQL mutation failed", "thread is already resolved", "PRRT_example", "NOT_FOUND"):
        assert detail in output


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


def state_comment(comment_id: int, records: list[dict], author: str = "github-actions[bot]") -> dict:
    return {
        "id": comment_id,
        "body": encode_lifecycle_state("octo/demo", 12, records),
        "user": {"login": author},
    }


def review_thread(thread_id: str, body: str, resolved: bool = False) -> dict:
    return {
        "id": thread_id,
        "is_resolved": resolved,
        "comments": [{"body": body, "path": "src/app.py", "author": "github-actions[bot]"}],
    }


def test_fingerprint_uses_code_location_and_comparison_matches_line_shifts():
    earlier = finding("Validate owner", 10, "if owner_id is None:\n    raise ValueError()")
    later = finding(" validate   OWNER ", 37, " if owner_id is None:  raise ValueError() ")
    earlier_id = finding_fingerprint(earlier, earlier["code_context"])
    later_id = finding_fingerprint(later, later["code_context"])

    assert earlier_id != later_id
    compared = compare_findings(
        [{
            **record(earlier_id, earlier["title"]),
            "file": earlier["file"],
            "category": earlier["category"],
            "issue_type": "security",
            "severity": earlier["severity"],
            "line": earlier["line"],
            "code_context": earlier["code_context"],
        }],
        [{**later, "finding_id": later_id, "issue_type": "security"}],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )
    assert len(compared["existing_findings"]) == 1
    assert compared["new_findings"] == []
    assert compared["resolved_findings"] == []


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


@pytest.mark.parametrize(
    ("old_title", "new_title"),
    [
        ("REST endpoints use verb-based paths", "Non-RESTful endpoint naming conventions"),
        ("PAT used in workflow secrets", "Use of PAT_TOKEN in workflow may grant excessive privileges"),
    ],
)
def test_paraphrased_finding_at_same_location_is_existing(old_title, new_title, capsys):
    old_finding = {
        **finding(old_title, 18, "run: echo ${{ secrets.PAT_TOKEN }}"),
        "issue_type": "security",
    }
    current_finding = {
        **finding(new_title, 18, "run: echo ${{ secrets.PAT_TOKEN }}"),
        "issue_type": "security",
    }
    old_id = finding_fingerprint(old_finding, old_finding["code_context"])
    current_id = finding_fingerprint(current_finding, current_finding["code_context"])

    compared = compare_findings(
        [{
            **record(old_id, old_title),
            "file": old_finding["file"],
            "category": old_finding["category"],
            "issue_type": old_finding["issue_type"],
            "severity": old_finding["severity"],
            "line": old_finding["line"],
            "code_context": old_finding["code_context"],
        }],
        [{**current_finding, "finding_id": current_id}],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )

    assert [item["title"] for item in compared["existing_findings"]] == [new_title]
    assert compared["new_findings"] == []
    assert compared["resolved_findings"] == []
    output = capsys.readouterr().out
    assert f"old_title={old_title!r}" in output
    assert f"new_title={new_title!r}" in output
    assert "match result=finding_id" in output


def test_one_code_edit_does_not_split_nearby_findings_into_new_and_resolved():
    previous = []
    current = []
    for line, finding_id in enumerate(("A", "B", "C"), start=30):
        old = {
            **record(finding_id, f"Old wording {finding_id}"),
            "file": "src/app.py",
            "category": "Security",
            "issue_type": "security",
            "severity": "HIGH",
            "line": line,
            "code_context": f"old nearby source context {finding_id}",
        }
        new = {
            **old,
            "finding_id": finding_fingerprint(
                {
                    **old,
                    "title": f"Reworded finding {finding_id}",
                    "line": line,
                },
                f"changed nearby source context {finding_id}",
            ),
            "title": f"Reworded finding {finding_id}",
            "code_context": f"changed nearby source context {finding_id}",
        }
        old["finding_id"] = f"legacy-context-hash-{finding_id}"
        previous.append(old)
        current.append(new)

    compared = compare_findings(
        previous,
        current,
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )

    assert len(compared["existing_findings"]) == 3
    assert compared["new_findings"] == []
    assert compared["resolved_findings"] == []


def test_comparison_classifies_new_existing_and_resolved_findings():
    previous = [{**record("A", "A"), "line": 1}, record("B", "B"), record("C", "C")]
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
        reviewed_line_scope={"src/app.py": {1}},
    )

    assert [item["finding_id"] for item in compared["new_findings"]] == ["D"]
    assert [item["finding_id"] for item in compared["existing_findings"]] == ["B", "C"]
    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert compared["resolved_findings"][0]["status"] == "resolved"
    records_by_id = {item["finding_id"]: item for item in compared["records"]}
    assert records_by_id["B"]["thread_id"] == "thread-B"


def test_seven_previous_three_current_resolves_only_finding_in_delete_employee_diff(capsys):
    previous = [
        {
            **record(f"OLD-{line}", f"Old finding at {line}"),
            "file": "src/employee.py",
            "category": "Security",
            "severity": "HIGH",
            "line": line,
        }
        for line in (2, 5, 8, 19, 25, 30, 40)
    ]
    current = [
        {**previous[0]},
        {**previous[1]},
        {
            **record("NEW-20", "Current deleteEmployee finding"),
            "file": "src/employee.py",
            "category": "Reliability",
            "severity": "MEDIUM",
            "line": 20,
        },
    ]

    compared = compare_findings(
        previous,
        current,
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/employee.py"],
        reviewed_line_scope={"src/employee.py": {19}},
    )

    assert len(previous) == 7
    assert len(current) == 3
    assert [finding["finding_id"] for finding in compared["resolved_findings"]] == ["OLD-19"]
    assert [finding["finding_id"] for finding in compared["new_findings"]] == ["NEW-20"]
    existing_by_id = {finding["finding_id"]: finding for finding in compared["existing_findings"]}
    assert set(existing_by_id) == {"OLD-2", "OLD-5", "OLD-8", "OLD-25", "OLD-30", "OLD-40"}
    assert all(
        existing_by_id[f"OLD-{line}"]["evaluation_status"] == "not_re_evaluated"
        for line in (8, 25, 30, 40)
    )
    output = capsys.readouterr().out
    assert "saved_file='src/employee.py' reviewed_scope='changed_old_line'" in output
    assert "saved_line=19" in output
    assert "reason=fixed" in output
    assert "saved_line=25" in output
    assert "reviewed_scope='not_changed'" in output
    assert "reason=not_re_evaluated" in output


def test_comparison_logs_duplicate_identity_discard(capsys):
    duplicate_records = [
        {**record("SAME", "First title"), "repository": "octo/demo", "pr_number": 12, "category": "Security"},
        {**record("SAME", "Duplicate title"), "repository": "octo/demo", "pr_number": 12, "category": "Security"},
    ]

    compared = compare_findings(
        [],
        duplicate_records,
        repository="octo/demo",
        pr_number=12,
        head_sha="head",
        reviewed_files=["src/app.py"],
    )

    assert len(compared["new_findings"]) == 1
    output = capsys.readouterr().out
    assert "stage=after_identity_dedup count=1 discarded=1" in output
    assert "dedup_discarded title='Duplicate title' category='Security'" in output
    assert "reason=duplicate finding_id SAME; last occurrence retained" in output


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


def test_resolve_unreviewed_override_cannot_resolve_without_ai_line_scope(capsys):
    compared = compare_findings(
        [{**record("A", "A"), "line": 12}],
        [],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
        resolve_unreviewed=True,
        reviewed_line_scope={},
    )

    assert compared["resolved_findings"] == []
    assert len(compared["existing_findings"]) == 1
    assert compared["existing_findings"][0]["evaluation_status"] == "not_re_evaluated"
    assert compared["records"][0]["status"] == "open"
    assert "resolve_unreviewed override ignored for resolution" in capsys.readouterr().out


def test_unchanged_context_finding_stays_existing_when_neighbor_is_changed(capsys):
    prompt_diff = (
        "FILE: src/app.py\n"
        "@@ -18,3 +18,3 @@ update\n"
        " unchanged line with prior finding\n"
        "-old changed line\n"
        "+new changed line\n"
        " unchanged line after\n"
    )
    compared = compare_findings(
        [{**record("CONTEXT", "Unchanged context finding"), "line": 18}],
        [],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
        reviewed_line_scope=build_reviewed_line_scope_from_prompt_diff(prompt_diff),
    )

    assert compared["resolved_findings"] == []
    assert compared["existing_findings"][0]["finding_id"] == "CONTEXT"
    assert compared["existing_findings"][0]["evaluation_status"] == "not_re_evaluated"
    assert "reviewed_scope='not_changed'" in capsys.readouterr().out


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


def test_find_state_comment_logs_every_candidate_and_accepts_bot_or_pat_author(capsys):
    records = [record("A", "A")]
    comments = [
        state_comment(11, records, author="github-actions[bot]"),
        state_comment(12, records, author="Mahalakshmi-M26"),
        state_comment(13, records, author="someone-else"),
        {
            **state_comment(14, records, author="github-actions[bot]"),
            "body": encode_lifecycle_state("octo/demo", 3, records),
        },
        {
            **state_comment(15, records, author="github-actions[bot]"),
            "body": "<!-- ai-review-finding-lifecycle:broken -->",
        },
    ]

    comment_id, loaded = GitHubMCPFindingLifecycle._find_state_comment(
        comments,
        "octo/demo",
        12,
    )

    assert comment_id == 12
    assert loaded == records
    output = capsys.readouterr().out
    assert "Inspecting 5 PR issue comments" in output
    assert "id=12" in output and "author='Mahalakshmi-M26'" in output
    assert "id=11" in output and "author='github-actions[bot]'" in output
    assert "State comment id=12 ACCEPTED: selected as newest valid lifecycle state" in output
    assert "State comment id=11 ACCEPTED: valid lifecycle state, but newer valid state already selected" in output
    assert "State comment id=13 REJECTED: author 'someone-else' is not in the accepted author list" in output
    assert "State comment id=14 REJECTED: PR number mismatch: stored=3 requested=12" in output
    assert "State comment id=15 REJECTED: lifecycle marker framing is missing or altered" in output


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

    assert earlier["finding_id"] != later["finding_id"]
    assert earlier["line"] == 2
    assert later["line"] == 3
    compared = compare_findings(
        [{**earlier, "repository": "octo/demo", "pr_number": 12}],
        [later],
        repository="octo/demo",
        pr_number=12,
        head_sha="new-head",
        reviewed_files=["src/app.py"],
    )
    assert len(compared["existing_findings"]) == 1
    assert compared["new_findings"] == []
    assert compared["resolved_findings"] == []


def test_reconcile_resolves_only_missing_finding_thread(monkeypatch):
    calls = []
    previous = [{**record("A", "A"), "line": 1}, {**record("B", "B"), "line": 2}]
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
        reviewed_line_scope={"src/app.py": {1}},
    ))

    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert [item["finding_id"] for item in compared["existing_findings"]] == ["B"]
    assert [item["finding_id"] for item in compared["new_findings"]] == ["D"]
    assert [(name, args["method"]) for name, args in calls if name == "pull_request_review_write"] == [
        ("pull_request_review_write", "resolve_thread"),
    ]
    assert not any(name in {"add_issue_comment", "update_issue_comment"} for name, _ in calls)


def test_reconcile_loads_pat_authored_state_and_resolves_removed_findings(monkeypatch, capsys):
    calls = []
    previous = [
        {**record("REMOVED", "Removed finding"), "thread_id": "thread-removed", "line": 1},
        {**record("CURRENT", "Current finding"), "thread_id": "thread-current", "line": 2},
    ]
    comments = [state_comment(91, previous, author="Mahalakshmi-M26")]
    threads = [
        review_thread("thread-removed", "old removed"),
        review_thread("thread-current", "still present"),
    ]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    compared = asyncio.run(manager.reconcile(
        "octo", "demo", 12, "new-head", [record("CURRENT", "Current finding")], ["src/app.py"],
        reviewed_line_scope={"src/app.py": {1}},
    ))

    assert [item["finding_id"] for item in compared["existing_findings"]] == ["CURRENT"]
    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["REMOVED"]
    assert compared["resolved_findings"][0]["thread_id"] == "thread-removed"
    output = capsys.readouterr().out
    assert "[P3] Lifecycle state location: PR issue comment octo/demo#12" in output
    assert "[P3] Loaded lifecycle state comment id=91 finding_count=2" in output
    assert "[P3] Loaded lifecycle finding IDs: ['REMOVED', 'CURRENT']" in output
    assert "[P3] Previous findings: 2" in output
    assert "[P3] Previous finding IDs: ['REMOVED', 'CURRENT']" in output


def test_reconcile_continues_and_persists_retryable_state_when_resolve_fails(monkeypatch, capsys):
    calls = []
    previous = [{**record("A", "A"), "line": 1}]
    comments = [state_comment(91, previous)]
    threads = [review_thread("thread-A", "old A")]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        if name == "pull_request_review_write" and arguments.get("method") == "resolve_thread":
            return CallToolResult(
                content=[TextContent(type="text", text="Resource not accessible by integration")],
                is_error=True,
            )
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    compared = asyncio.run(manager.reconcile(
        "octo", "demo", 12, "new-head", [], ["src/app.py"],
        reviewed_line_scope={"src/app.py": {1}},
    ))

    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert compared["records"][0]["status"] == "open"
    assert compared["records"][0]["thread_resolution_pending"] is True
    assert [(name, args["method"]) for name, args in calls if name == "pull_request_review_write"] == [
        ("pull_request_review_write", "resolve_thread"),
    ]

    asyncio.run(manager.finalize("octo", "demo", 12, compared))
    state_update = next(
        arguments for name, arguments in calls if name == "update_issue_comment"
    )
    saved_records = decode_lifecycle_state(state_update["body"], "octo/demo", 12)
    assert saved_records is not None
    assert saved_records[0]["status"] == "open"
    assert saved_records[0]["thread_resolution_pending"] is True

    output = capsys.readouterr().out
    assert "[P3] WARNING: Failed to resolve thread thread-A. Continuing." in output
    assert "Resource not accessible by integration" in output


def test_out_of_scope_previous_finding_is_existing_without_unresolving_thread(monkeypatch):
    calls = []
    previous = [{**record("OUTSIDE", "Outside hunk"), "line": 80}]
    comments = [state_comment(91, previous)]
    threads = [review_thread("thread-OUTSIDE", "outside hunk", resolved=True)]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    compared = asyncio.run(manager.reconcile(
        "octo",
        "demo",
        12,
        "new-head",
        [],
        ["src/app.py"],
        reviewed_line_scope={"src/app.py": {10, 11, 12}},
    ))

    assert [item["finding_id"] for item in compared["existing_findings"]] == ["OUTSIDE"]
    assert compared["existing_findings"][0]["evaluation_status"] == "not_re_evaluated"
    assert compared["resolved_findings"] == []
    assert not any(name == "pull_request_review_write" for name, _ in calls)


def test_reconcile_does_not_suppress_unrelated_mcp_tool_errors(monkeypatch):
    calls = []
    comments = [state_comment(91, [record("A", "A")])]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return CallToolResult(
                content=[TextContent(type="text", text="review-thread read denied")],
                is_error=True,
            )
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    manager = GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token"))

    with pytest.raises(ExceptionGroup) as error_group:
        asyncio.run(manager.reconcile(
            "octo", "demo", 12, "new-head", [], ["src/app.py"],
        ))
    assert any(
        isinstance(error, RuntimeError)
        and "get_review_comments" in str(error)
        and "tool error" in str(error)
        for error in error_group.value.exceptions
    )


def test_reconcile_backfills_legacy_thread_before_resolving(monkeypatch, capsys):
    calls = []
    finding_id = "legacy-finding"
    thread_id = "PRRT_legacy-thread"
    previous = [{
        **record(finding_id, "Old finding"),
        "thread_id": None,
        "inline_body": "Original comment wording",
        "status": "open",
        "line": 1,
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
        reviewed_line_scope={"src/app.py": {1}},
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


def test_reconcile_matches_findings_from_actions_and_pat_user_authors(monkeypatch):
    calls = []
    previous = [
        {**record("A", "A"), "thread_id": None, "line": 1},
        {**record("B", "B"), "thread_id": None, "line": 2},
    ]
    comments = [state_comment(91, previous)]
    threads = [
        {
            **review_thread("thread-A", "Finding A\n\n<!-- ai-review-finding-id:A -->"),
            "comments": [{
                "body": "Finding A\n\n<!-- ai-review-finding-id:A -->",
                "path": "src/app.py",
                "author": "github-actions",
            }],
        },
        {
            **review_thread("thread-B", "Finding B\n\n<!-- ai-review-finding-id:B -->"),
            "comments": [{
                "body": "Finding B\n\n<!-- ai-review-finding-id:B -->",
                "path": "src/app.py",
                "author": "Mahalakshmi-M26",
            }],
        },
    ]

    def responder(name, arguments):
        if arguments.get("method") == "get_comments":
            return mcp_payload(comments)
        if arguments.get("method") == "get_review_comments":
            return mcp_payload({"review_threads": threads, "pageInfo": {"hasNextPage": False}})
        return mcp_json_text({"message": "ok"})

    install_fake_lifecycle_mcp(monkeypatch, responder, calls)
    compared = asyncio.run(GitHubMCPFindingLifecycle(GitHubMCPProvider(token="test-token")).reconcile(
        "octo", "demo", 12, "new-head", [record("B", "B")], ["src/app.py"],
        reviewed_line_scope={"src/app.py": {1}},
    ))

    assert [item["finding_id"] for item in compared["existing_findings"]] == ["B"]
    assert [item["finding_id"] for item in compared["resolved_findings"]] == ["A"]
    assert [item["finding_id"] for item in compared["new_findings"]] == []
    assert {item["finding_id"]: item["thread_id"] for item in compared["records"]} == {
        "A": "thread-A",
        "B": "thread-B",
    }


@pytest.mark.parametrize(
    ("author", "body", "path", "expected"),
    [
        ("Mahalakshmi-M26", "marker: <!-- ai-review-finding-id:A -->", "src/app.py", True),
        ("Mahalakshmi-M26", "marker: <!-- ai-review-finding-id:other -->", "src/app.py", False),
        ("Mahalakshmi-M26", "marker: <!-- ai-review-finding-id:A -->", "src/other.py", False),
        ("untrusted-user", "marker: <!-- ai-review-finding-id:A -->", "src/app.py", False),
    ],
)
def test_comment_matching_keeps_marker_path_and_author_checks(author, body, path, expected):
    thread = {
        "id": "thread-A",
        "comments": [{"body": body, "path": path, "author": author}],
    }

    assert GitHubMCPFindingLifecycle._comment_matches(
        thread,
        {"finding_id": "A", "file": "src/app.py"},
    ) is expected


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


def test_finalize_persists_new_thread_id(monkeypatch, capsys):
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
    output = capsys.readouterr().out
    assert "[P3] Lifecycle state location: PR issue comment octo/demo#12" in output
    assert "[P3] Saved lifecycle finding IDs: ['D']" in output
    assert "[P3] Lifecycle state saved in PR issue comment id=91" in output


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
            "line": 20,
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
        reviewed_line_scope={"src/app.py": {20}},
    ))

    assert [item["finding_id"] for item in second_review["resolved_findings"]] == [finding_id]
    assert second_review["resolved_findings"][0]["thread_id"] == thread_id
    assert any(
        name == "pull_request_review_write"
        and arguments.get("method") == "resolve_thread"
        and arguments.get("threadId") == thread_id
        for name, arguments in calls
    )