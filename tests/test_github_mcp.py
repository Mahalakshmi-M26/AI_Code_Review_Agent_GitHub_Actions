import asyncio
import json

import pytest
from mcp.types import CallToolResult, TextContent

from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.review import filter_changed_files


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
    install_fake_mcp(monkeypatch, [files_result([]), text_result(full_diff)], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))

    assert [item["filename"] for item in files] == ["modified.py", "new.py", "renamed.py", "deleted.py"]
    assert all(item["patch"].startswith("@@") for item in files)


def test_get_diff_parses_structured_diff_text(monkeypatch):
    calls = []
    full_diff = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+created"
    result = CallToolResult(content=[], structured_content={"diff": full_diff})
    install_fake_mcp(monkeypatch, [files_result([]), result], calls)

    files = asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))

    assert files[0]["filename"] == "new.py"
    assert "+created" in files[0]["patch"]


def test_missing_patch_and_unparseable_diff_fails_clearly(monkeypatch):
    calls = []
    install_fake_mcp(monkeypatch, [files_result([{"filename": "src/app.py"}]), text_result("not a unified diff")], calls)

    with pytest.raises(RuntimeError, match="no parseable file diffs"):
        asyncio.run(GitHubMCPProvider(token="abc123").get_pull_request_files("octo", "demo", 9))


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
