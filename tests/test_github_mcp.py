import asyncio

import pytest

from reviewer.providers.github_mcp import GitHubMCPProvider


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
