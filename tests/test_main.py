import asyncio

import reviewer.main as reviewer_main


def test_metadata_only_pull_request_posts_advisory_without_calling_gep(monkeypatch):
    review_calls = []

    class FakeProvider:
        def __init__(self, token):
            assert token == "test-token"

        async def get_pull_request_files(self, owner, repo, pull_number):
            assert (owner, repo, pull_number) == ("octo", "demo", 12)
            return [
                {
                    "filename": ".github/gradle-build.yml",
                    "status": "renamed",
                    "patch": "",
                    "review_skip_reason": "rename-only / no content changes",
                },
                {
                    "filename": ".github/maven-build.yml",
                    "status": "renamed",
                    "patch": "",
                    "review_skip_reason": "rename-only / no content changes",
                },
            ]

        async def post_review(self, owner, repo, pull_number, review_payload):
            review_calls.append((owner, repo, pull_number, review_payload))

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/demo")
    monkeypatch.setenv("PR_NUMBER", "12")
    monkeypatch.delenv("GEP_API_KEY", raising=False)
    monkeypatch.setattr(reviewer_main, "load_event", lambda: {
        "pull_request": {
            "number": 12,
            "head": {"sha": "head-sha"},
            "base": {"sha": "base-sha"},
        }
    })
    monkeypatch.setattr(reviewer_main, "GitHubMCPProvider", FakeProvider)
    monkeypatch.setattr(
        reviewer_main,
        "call_capgemini",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("GEP must not be called for metadata-only PRs")),
    )

    reviewer_main.main()

    assert len(review_calls) == 1
    owner, repo, pull_number, payload = review_calls[0]
    assert (owner, repo, pull_number) == ("octo", "demo", 12)
    assert payload["commit_id"] == "head-sha"
    assert payload["comments"] == []
    assert "No reviewable code changes detected." in payload["body"]
    assert ".github/gradle-build.yml - rename-only / no content changes" in payload["body"]
    assert ".github/maven-build.yml - rename-only / no content changes" in payload["body"]
    assert "Human approval remains required." in payload["body"]