import asyncio
import json

import pytest
from mcp.types import CallToolResult, TextContent

from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.models import ReviewResult
from reviewer.review import (
    build_github_review_payload,
    filter_changed_files,
    validate_finding_lines,
)


def install_fake_mcp(monkeypatch, results, calls):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            self.results = iter(results)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return {"serverInfo": {"name": "github-mcp-server", "version": "2.0.1"}}

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            result = next(self.results)
            if isinstance(result, Exception):
                raise result
            return result

    class FakeStdioClient:
        async def __aenter__(self):
            return object(), object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    import reviewer.providers.github_mcp as github_mcp_module

    monkeypatch.setattr(github_mcp_module, "stdio_client", lambda params: FakeStdioClient())
    monkeypatch.setattr(github_mcp_module, "ClientSession", FakeSession)


def files_result(files):
    return CallToolResult(content=[], structured_content={"files": files})


def text_result(text):
    return CallToolResult(content=[TextContent(type="text", text=text)])


def make_review_result(findings=None):
    return ReviewResult.model_validate(
        {
            "decision": "ADVISORY",
            "risk_level": "MEDIUM",
            "findings": findings or [],
            "summary": "Review summary",
            "files_reviewed": ["src/app.py"],
            "files_skipped": [],
            "categories_reviewed": ["Security"],
        }
    )


def make_finding(line):
    return {
        "severity": "HIGH",
        "category": "Security",
        "file": "src/app.py",
        "line": line,
        "title": "Validate input",
        "issue": "Input is not validated.",
        "recommendation": "Validate before use.",
        "suggested_fix": "value = validate(value)",
    }


def successful_write_results(count):
    return [CallToolResult(content=[], is_error=False) for _ in range(count)]


def test_server_parameters_use_official_image_and_toolsets():
    provider = GitHubMCPProvider(token="abc123", toolsets=["repos", "pull_requests"])

    params = provider.build_server_parameters()

    assert params.command == "docker"
    assert "ghcr.io/github/github-mcp-server" in params.args
    assert params.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "abc123"
    assert params.env["GITHUB_TOOLSETS"] == "repos,pull_requests"


def test_initialize_and_discover_tools(monkeypatch):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            self.initialized = False
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.closed = True

        async def initialize(self):
            self.initialized = True
            return {"serverInfo": {"name": "github-mcp-server", "version": "1.2.3"}}

        async def list_tools(self):
            return {
                "tools": [
                    {
                        "name": "pull_request_read",
                        "description": "Read pull request details",
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "repository_get_file_contents",
                        "description": "Read repo file contents",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }

    class FakeStdioClient:
        def __init__(self, params):
            self.params = params

        async def __aenter__(self):
            return (object(), object())

        async def __aexit__(self, exc_type, exc, tb):
            return None

    import reviewer.providers.github_mcp as github_mcp_module

    monkeypatch.setattr(github_mcp_module, "stdio_client", lambda params: FakeStdioClient(params))
    monkeypatch.setattr(github_mcp_module, "ClientSession", FakeSession)

    provider = GitHubMCPProvider(token="abc123")

    result = asyncio.run(provider.initialize())
    tools = asyncio.run(provider.discover_tools())

    assert result["serverInfo"]["name"] == "github-mcp-server"
    assert provider.server_name == "github-mcp-server"
    assert provider.server_version == "1.2.3"
    assert any(tool["name"] == "pull_request_read" for tool in tools)


def test_pr_tool_metadata_extraction_filters_pull_related_tools():
    tool_list = [
        {"name": "repository_get_file_contents", "description": "Read files", "inputSchema": {"type": "object"}},
        {"name": "pull_request_read", "description": "Read PR details", "inputSchema": {"type": "object"}},
        {"name": "create_issue", "description": "Create issue", "inputSchema": {"type": "object"}},
        {"name": "pull_request_review_write", "description": "Submit review", "inputSchema": {"type": "object"}},
    ]

    tools = GitHubMCPProvider.extract_pr_tools(tool_list)

    assert [tool["name"] for tool in tools] == [
        "repository_get_file_contents",
        "pull_request_read",
        "pull_request_review_write",
    ]


def test_connection_failure_raises_clear_error(monkeypatch):
    import reviewer.providers.github_mcp as github_mcp_module

    def boom(_params):
        raise RuntimeError("docker unavailable")

    monkeypatch.setattr(github_mcp_module, "stdio_client", boom)

    provider = GitHubMCPProvider(token="abc123")

    with pytest.raises(RuntimeError, match="MCP connection failed"):
        asyncio.run(provider.initialize())


def test_initialization_failure_raises_clear_error(monkeypatch):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            raise RuntimeError("initialize failed")

    class FakeStdioClient:
        async def __aenter__(self):
            return (object(), object())

        async def __aexit__(self, exc_type, exc, tb):
            return None

    import reviewer.providers.github_mcp as github_mcp_module

    monkeypatch.setattr(github_mcp_module, "stdio_client", lambda params: FakeStdioClient())
    monkeypatch.setattr(github_mcp_module, "ClientSession", FakeSession)

    provider = GitHubMCPProvider(token="abc123")

    with pytest.raises(RuntimeError, match="MCP initialization failed"):
        asyncio.run(provider.initialize())


def test_tool_discovery_failure_raises_clear_error(monkeypatch):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return {"serverInfo": {"name": "github-mcp-server", "version": "1.2.3"}}

        async def list_tools(self):
            raise RuntimeError("list_tools failed")

    class FakeStdioClient:
        async def __aenter__(self):
            return (object(), object())

        async def __aexit__(self, exc_type, exc, tb):
            return None

    import reviewer.providers.github_mcp as github_mcp_module

    monkeypatch.setattr(github_mcp_module, "stdio_client", lambda params: FakeStdioClient())
    monkeypatch.setattr(github_mcp_module, "ClientSession", FakeSession)

    provider = GitHubMCPProvider(token="abc123")

    with pytest.raises(RuntimeError, match="MCP tool discovery failed"):
        asyncio.run(provider.discover_tools())


def test_provider_does_not_use_rest_api():
    provider = GitHubMCPProvider(token="abc123")

    assert not hasattr(provider, "github_request")
    assert "api.github.com" not in str(provider)


def test_get_pull_request_files_calls_real_tool_with_expected_arguments(monkeypatch):
    calls = []
    patch = "@@ -1 +1,2 @@\n old\n+new"
    install_fake_mcp(monkeypatch, [files_result([{"filename": "src/app.py", "patch": patch}])], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))

    assert calls == [(
        "pull_request_read",
        {"method": "get_files", "owner": "octo", "repo": "demo", "pullNumber": 42, "page": 1, "perPage": 100},
    )]
    assert files == [{"filename": "src/app.py", "patch": patch}]


def test_get_files_parses_actual_call_tool_result_and_multiple_files(monkeypatch):
    calls = []
    files_payload = [
        {"filename": "src/one.py", "patch": "@@ -0,0 +1 @@\n+one"},
        {"filename": "src/two.py", "patch": "@@ -1 +1 @@\n-old\n+two"},
    ]
    install_fake_mcp(monkeypatch, [files_result(files_payload)], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 7))

    assert [item["filename"] for item in files] == ["src/one.py", "src/two.py"]
    assert files[1]["patch"] == files_payload[1]["patch"]


def test_get_files_parses_text_content_result(monkeypatch):
    calls = []
    payload = [{"filename": "src/app.py", "patch": "@@ -0,0 +1 @@\n+ok"}]
    install_fake_mcp(monkeypatch, [text_result(json.dumps(payload))], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 7))

    assert files[0]["filename"] == "src/app.py"
    assert files[0]["patch"].endswith("+ok")


def test_get_files_paginates_all_pages(monkeypatch):
    calls = []
    first_page = [{"filename": f"src/{index}.py", "patch": "@@ -0,0 +1 @@\n+ok"} for index in range(100)]
    second_page = [{"filename": "src/last.py", "patch": "@@ -0,0 +1 @@\n+last"}]
    install_fake_mcp(monkeypatch, [files_result(first_page), files_result(second_page)], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 7))

    assert len(files) == 101
    assert calls[0][1]["page"] == 1
    assert calls[1][1]["page"] == 2
    assert all(call[1]["perPage"] == 100 for call in calls)


def test_get_files_without_patches_falls_back_to_mcp_get_diff(monkeypatch):
    calls = []
    full_diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 1111111..2222222 100644\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n old\n+new\n"
    )
    install_fake_mcp(
        monkeypatch,
        [files_result([{"filename": "src/app.py"}]), text_result(full_diff)],
        calls,
    )

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 8))

    assert [call[1]["method"] for call in calls] == ["get_files", "get_diff"]
    assert files == [{"filename": "src/app.py", "patch": "@@ -1 +1,2 @@\n old\n+new"}]


def test_get_diff_splits_modified_new_renamed_and_deleted_files(monkeypatch):
    calls = []
    full_diff = (
        "diff --git a/modified.py b/modified.py\n--- a/modified.py\n+++ b/modified.py\n@@ -1 +1,2 @@\n-old\n+new\n"
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+created\n"
        "diff --git a/old.py b/renamed.py\nsimilarity index 100%\nrename from old.py\nrename to renamed.py\n--- a/old.py\n+++ b/renamed.py\n@@ -1 +1 @@\n same\n"
        "diff --git a/deleted.py b/deleted.py\ndeleted file mode 100644\n--- a/deleted.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    )
    install_fake_mcp(
        monkeypatch,
        [
            files_result([
                {"filename": "modified.py"},
                {"filename": "new.py"},
                {"filename": "renamed.py"},
                {"filename": "deleted.py"},
            ]),
            text_result(full_diff),
        ],
        calls,
    )

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))

    assert [item["filename"] for item in files] == ["modified.py", "new.py", "renamed.py", "deleted.py"]
    assert all(item["patch"].startswith("@@") for item in files)


def test_get_diff_parses_structured_diff_text(monkeypatch):
    calls = []
    full_diff = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+created"
    result = CallToolResult(content=[], structured_content={"diff": full_diff})
    install_fake_mcp(monkeypatch, [files_result([{"filename": "new.py"}]), result], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))

    assert files[0]["filename"] == "new.py"
    assert "+created" in files[0]["patch"]


def test_get_diff_parses_json_wrapped_diff_from_text_content(monkeypatch):
    calls = []
    full_diff = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+created"
    install_fake_mcp(
        monkeypatch,
        [files_result([{"filename": "new.py"}]), text_result(json.dumps({"diff": full_diff}))],
        calls,
    )

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))

    assert files == [{"filename": "new.py", "patch": "@@ -0,0 +1 @@\n+created"}]


def test_missing_patch_and_unparseable_diff_fails_clearly(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [files_result([{"filename": "src/app.py"}]), text_result("not a unified diff")], calls)

    with pytest.raises(RuntimeError, match="diff normalization failure.*zero recognized file sections"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))


def test_two_file_get_diff_uses_metadata_and_preserves_added_line_numbers(monkeypatch):
    calls = []
    metadata = [
        {"filename": "src/A.java", "status": "modified"},
        {"filename": "src/B.java", "status": "modified"},
    ]
    full_diff = (
        "diff --git a/src/A.java b/src/A.java\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/A.java\n+++ b/src/A.java\n"
        "@@ -1,2 +1,2 @@\n-old\n+new\n keep\n"
        "diff --git a/src/B.java b/src/B.java\n"
        "index 3333333..4444444 100644\n"
        "--- a/src/B.java\n+++ b/src/B.java\n"
        "@@ -10 +10,2 @@\n context\n+added\n"
    )
    install_fake_mcp(monkeypatch, [files_result(metadata), text_result(full_diff)], calls)

    normalized = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))
    filtered = filter_changed_files(normalized)

    assert [item["filename"] for item in normalized] == ["src/A.java", "src/B.java"]
    assert normalized[0]["patch"] == "@@ -1,2 +1,2 @@\n-old\n+new\n keep"
    assert normalized[1]["patch"] == "@@ -10 +10,2 @@\n context\n+added"
    assert filtered["files_reviewed"] == ["src/A.java", "src/B.java"]
    assert filtered["changed_line_map"] == {
        "src/A.java": [{"line": 1, "content": "new"}],
        "src/B.java": [{"line": 11, "content": "added"}],
    }
    assert [call[1]["method"] for call in calls] == ["get_files", "get_diff"]


def test_get_diff_paths_with_spaces_match_get_files_metadata(monkeypatch):
    calls = []
    full_diff = (
        'diff --git "a/src/A file.java" "b/src/A file.java"\n'
        '--- "a/src/A file.java"\n+++ "b/src/A file.java"\n'
        "@@ -1 +1,2 @@\n old\n+new\n"
    )
    install_fake_mcp(
        monkeypatch,
        [files_result([{"filename": "src/A file.java"}]), text_result(full_diff)],
        calls,
    )

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))

    assert files == [{"filename": "src/A file.java", "patch": "@@ -1 +1,2 @@\n old\n+new"}]


def test_rename_only_metadata_returns_empty_patch_with_explicit_skip_reason(monkeypatch):
    calls = []
    full_diff = (
        "diff --git a/.github/workflows/gradle-build.yml b/.github/gradle-build.yml\n"
        "similarity index 100%\n"
        "rename from .github/workflows/gradle-build.yml\n"
        "rename to .github/gradle-build.yml\n"
    )
    metadata = [{
        "filename": ".github/gradle-build.yml",
        "status": "renamed",
    }]
    install_fake_mcp(monkeypatch, [files_result(metadata), text_result(full_diff)], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))
    filtered = filter_changed_files(files)

    assert files == [{
        "filename": ".github/gradle-build.yml",
        "status": "renamed",
        "patch": "",
        "review_skip_reason": "rename-only / no content changes",
    }]
    assert filtered["files_reviewed"] == []
    assert filtered["files_skipped"] == [".github/gradle-build.yml (no patch)"]


def test_mixed_metadata_only_and_source_files_keeps_source_patch_reviewable(monkeypatch):
    calls = []
    full_diff = (
        "diff --git a/.github/workflows/gradle-build.yml b/.github/gradle-build.yml\n"
        "similarity index 100%\n"
        "rename from .github/workflows/gradle-build.yml\n"
        "rename to .github/gradle-build.yml\n"
        "diff --git a/src/Main.java b/src/Main.java\n"
        "--- a/src/Main.java\n+++ b/src/Main.java\n"
        "@@ -1 +1,2 @@\n old\n+new\n"
        "diff --git a/pom.xml b/pom.xml\nold mode 100644\nnew mode 100755\n"
    )
    metadata = [
        {"filename": ".github/gradle-build.yml", "status": "renamed"},
        {"filename": "src/Main.java", "status": "modified"},
        {"filename": "pom.xml", "status": "modified"},
    ]
    install_fake_mcp(monkeypatch, [files_result(metadata), text_result(full_diff)], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))
    filtered = filter_changed_files(files)

    assert filtered["files_reviewed"] == ["src/Main.java"]
    assert filtered["changed_line_map"] == {"src/Main.java": [{"line": 2, "content": "new"}]}
    assert files[0]["review_skip_reason"] == "rename-only / no content changes"
    assert files[2]["review_skip_reason"] == "metadata-only / no content changes"


def test_get_diff_metadata_and_section_count_mismatch_fails_clearly(monkeypatch):
    calls = []
    full_diff = "diff --git a/src/A.java b/src/A.java\n--- a/src/A.java\n+++ b/src/A.java\n@@ -0,0 +1 @@\n+new"
    install_fake_mcp(
        monkeypatch,
        [files_result([{"filename": "src/A.java"}, {"filename": "src/B.java"}]), text_result(full_diff)],
        calls,
    )

    with pytest.raises(RuntimeError, match="metadata reported 2 files but get_diff contained 1 file sections"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))


def test_get_diff_filename_mismatch_fails_instead_of_reviewing_incomplete_data(monkeypatch):
    calls = []
    full_diff = "diff --git a/src/Other.java b/src/Other.java\n--- a/src/Other.java\n+++ b/src/Other.java\n@@ -0,0 +1 @@\n+new"
    install_fake_mcp(
        monkeypatch,
        [files_result([{"filename": "src/A.java"}]), text_result(full_diff)],
        calls,
    )

    with pytest.raises(RuntimeError, match="did not match get_files metadata"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))


def test_get_diff_empty_or_missing_text_fails_as_parse_error(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [files_result([]), CallToolResult(content=[])], calls)

    with pytest.raises(RuntimeError, match="MCP get_diff parse failure.*no structured content or text content"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))


def test_get_diff_tool_error_is_distinguished_from_parse_failure(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [files_result([]), CallToolResult(content=[], is_error=True)], calls)

    with pytest.raises(RuntimeError, match="MCP get_diff tool failure"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))


def test_diff_normalization_failure_logs_type_and_message_before_context_exit(monkeypatch, capsys):
    calls = []
    install_fake_mcp(monkeypatch, [files_result([]), text_result("not a diff")], calls)

    with pytest.raises(RuntimeError, match="MCP diff normalization failure") as error:
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 42))

    output = capsys.readouterr().out
    assert "[MCP] get_diff result type:" in output
    assert "mcp_types._types.CallToolResult" in output
    assert "[MCP] structured_content present:" in output
    assert "[MCP] content item count:" in output
    assert "[MCP] content[0] text length: 10" in output
    assert "[MCP] Unified diff text source:" in output
    assert "content[0].text" in output
    assert "[MCP] Unified diff starts with diff header:" in output
    assert "[MCP] MCP diff normalization failed" in output
    assert "Exception type: RuntimeError" in output
    assert "Exception message: MCP get_diff response contained zero recognized file sections." in output
    assert "unhandled errors in a TaskGroup" not in str(error.value)


def test_diff_structure_diagnostics_log_headers_and_matches_without_hunk_body(capsys):
    diff_text = (
        "diff --git a/src/A.java b/src/A.java\n"
        "index abc..def 100644\n"
        "--- a/src/A.java\n"
        "+++ b/src/A.java\n"
        "@@ -1 +1,2 @@\n"
        " public context must not print\n"
        "+secret_added_source must not print\n"
        "-secret_removed_source must not print\n"
    )
    metadata = [{"filename": "src/A.java", "status": "modified"}]

    GitHubMCPProvider._log_diff_normalization_diagnostics(diff_text, metadata)

    output = capsys.readouterr().out
    assert "[MCP-DIFF-DIAG] diff --git a/src/A.java b/src/A.java" in output
    assert "[MCP-DIFF-DIAG] index abc..def 100644" in output
    assert "[MCP-DIFF-DIAG] --- a/src/A.java" in output
    assert "[MCP-DIFF-DIAG] +++ b/src/A.java" in output
    assert "[MCP-DIFF-DIAG] Metadata status:\n[MCP-DIFF-DIAG] modified" in output
    assert "[MCP-DIFF-DIAG] Parsed old path:\n[MCP-DIFF-DIAG] a/src/A.java" in output
    assert "[MCP-DIFF-DIAG] Parsed new path:\n[MCP-DIFF-DIAG] b/src/A.java" in output
    assert "[MCP-DIFF-DIAG] Candidate filename:\n[MCP-DIFF-DIAG] src/A.java" in output
    assert "[MCP-DIFF-DIAG] Hunk header found:\n[MCP-DIFF-DIAG] true" in output
    assert "[MCP-DIFF-DIAG] Metadata file count:\n[MCP-DIFF-DIAG] 1" in output
    assert "[MCP-DIFF-DIAG] Parsed diff section count:\n[MCP-DIFF-DIAG] 1" in output
    assert "[MCP-DIFF-DIAG] Usable patch count:\n[MCP-DIFF-DIAG] 1" in output
    assert "[MCP-DIFF-DIAG] Match:\n[MCP-DIFF-DIAG] true" in output
    assert "must not print" not in output


def test_mcp_tool_error_fails_without_rest_fallback(monkeypatch):
    calls = []
    error_result = CallToolResult(content=[], is_error=True)
    install_fake_mcp(monkeypatch, [error_result], calls)

    with pytest.raises(RuntimeError, match="get_files.*tool error"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))
    assert len(calls) == 1
    assert calls[0][1]["method"] == "get_files"


def test_malformed_get_files_response_fails_clearly(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [files_result({"unexpected": "payload"})], calls)

    with pytest.raises(RuntimeError, match="did not contain a files list"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))


def test_mcp_normalized_patch_feeds_filter_and_deterministic_line_map(monkeypatch):
    calls = []
    patch = "@@ -4,2 +4,3 @@\n context\n+added = True\n context"
    install_fake_mcp(monkeypatch, [files_result([{"filename": "src/app.py", "patch": patch}])], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))
    filtered = filter_changed_files(files)

    assert filtered["files_reviewed"] == ["src/app.py"]
    assert filtered["changed_line_map"] == {"src/app.py": [{"line": 5, "content": "added = True"}]}


def test_provider_read_has_no_rest_endpoint_fallback():
    source = GitHubMCPProvider.get_pull_request_files.__code__.co_names

    assert "github_request" not in source
    assert "api.github.com" not in source


def test_post_review_creates_adds_multiple_comments_and_submits(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, successful_write_results(4), calls)
    line_map = {"src/app.py": [{"line": 8, "content": "value = request.data"}, {"line": 9, "content": "save(value)"}]}
    result = make_review_result([make_finding(8), {**make_finding(9), "title": "Validate before saving"}])
    validated = validate_finding_lines(result, line_map)
    payload = build_github_review_payload(validated, line_map, "head-sha-123")

    asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert calls[0] == (
        "pull_request_review_write",
        {"method": "create", "owner": "octo", "repo": "demo", "pullNumber": 42, "commitID": "head-sha-123"},
    )
    assert "event" not in calls[0][1]
    assert calls[1][0] == "add_comment_to_pending_review"
    assert calls[1][1] == {
        "owner": "octo",
        "repo": "demo",
        "pullNumber": 42,
        "path": "src/app.py",
        "body": payload["comments"][0]["body"],
        "line": 8,
        "side": "RIGHT",
        "subjectType": "LINE",
    }
    assert calls[2][0] == "add_comment_to_pending_review"
    assert calls[2][1]["line"] == 9
    assert calls[2][1]["body"] == payload["comments"][1]["body"]
    assert calls[3] == (
        "pull_request_review_write",
        {
            "method": "submit_pending",
            "owner": "octo",
            "repo": "demo",
            "pullNumber": 42,
            "body": payload["body"],
            "event": "COMMENT",
        },
    )


def test_post_review_with_no_findings_submits_summary_without_inline_calls(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, successful_write_results(2), calls)
    payload = build_github_review_payload(make_review_result(), {}, None)

    asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert len(calls) == 2
    assert calls[0][1] == {"method": "create", "owner": "octo", "repo": "demo", "pullNumber": 42}
    assert calls[1][1]["method"] == "submit_pending"
    assert calls[1][1]["body"] == payload["body"]
    assert calls[1][1]["event"] == "COMMENT"


def test_invalid_line_remains_summary_only_and_is_not_sent_as_inline(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, successful_write_results(2), calls)
    line_map = {"src/app.py": [{"line": 8, "content": "value = request.data"}]}
    result = validate_finding_lines(make_review_result([make_finding(999)]), line_map)
    payload = build_github_review_payload(result, line_map, "head-sha")

    asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert result.findings[0].line is None
    assert "comments" not in payload
    assert [call[1]["method"] for call in calls] == ["create", "submit_pending"]


def test_inline_comment_failure_cleans_up_pending_review(monkeypatch):
    calls = []
    install_fake_mcp(
        monkeypatch,
        [successful_write_results(1)[0], RuntimeError("inline broke"), successful_write_results(1)[0]],
        calls,
    )
    payload = {"body": "summary", "commit_id": "head", "comments": [{"path": "src/app.py", "line": 8, "side": "RIGHT", "body": "finding"}]}

    with pytest.raises(RuntimeError, match="inline broke"):
        asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert calls[0][1]["method"] == "create"
    assert calls[1][0] == "add_comment_to_pending_review"
    assert calls[2][1]["method"] == "delete_pending"
    assert calls[-1][0] == "pull_request_review_write"


def test_submit_failure_cleans_up_pending_review(monkeypatch):
    calls = []
    install_fake_mcp(
        monkeypatch,
        [successful_write_results(1)[0], RuntimeError("submit broke"), successful_write_results(1)[0]],
        calls,
    )
    payload = {"body": "summary", "comments": []}

    with pytest.raises(RuntimeError, match="submit broke"):
        asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert [call[1]["method"] for call in calls] == ["create", "submit_pending", "delete_pending"]


def test_cleanup_failure_does_not_hide_original_publication_failure(monkeypatch):
    calls = []
    install_fake_mcp(
        monkeypatch,
        [successful_write_results(1)[0], RuntimeError("original inline failure"), RuntimeError("cleanup failure")],
        calls,
    )
    payload = {"body": "summary", "comments": [{"path": "src/app.py", "line": 8, "body": "finding"}]}

    with pytest.raises(RuntimeError, match="original inline failure.*cleanup also failed:.*cleanup failure"):
        asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, payload))

    assert calls[-1][1]["method"] == "delete_pending"


def test_create_mcp_tool_error_fails_clearly_without_rest_fallback(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [CallToolResult(content=[], is_error=True)], calls)

    with pytest.raises(RuntimeError, match="pull_request_review_write.*create.*tool error"):
        asyncio.run(GitHubMCPProvider(token="abc123").post_review("octo", "demo", 42, {"body": "summary"}))

    assert len(calls) == 1
    assert calls[0][1]["method"] == "create"


def test_provider_review_write_has_no_rest_fallback():
    source = GitHubMCPProvider.post_review.__code__.co_names

    assert "github_request" not in source
    assert "api.github.com" not in source
