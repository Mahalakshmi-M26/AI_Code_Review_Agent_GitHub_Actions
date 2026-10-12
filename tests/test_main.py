import asyncio

import reviewer.main as reviewer_main
from reviewer.models import ReviewResult
from reviewer.review import build_github_review_payload


def install_fake_lifecycle(monkeypatch):
    class FakeLifecycle:
        def __init__(self, provider):
            pass

        async def reconcile(self, **kwargs):
            return {
                "records": [],
                "new_findings": [],
                "existing_findings": [],
                "resolved_findings": [],
                "reopened_findings": [],
                "thread_ids_before": set(),
                "state_comment_id": None,
            }

        async def finalize(self, **kwargs):
            return kwargs["comparison"]

    monkeypatch.setattr(reviewer_main, "GitHubMCPFindingLifecycle", FakeLifecycle)


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
    install_fake_lifecycle(monkeypatch)
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


def test_zero_changed_files_posts_no_change_advisory_without_gep(monkeypatch, capsys):
    review_calls = []
    read_calls = []

    class FakeProvider:
        def __init__(self, token):
            assert token == "test-token"

        async def get_pull_request_files(self, owner, repo, pull_number):
            read_calls.append((owner, repo, pull_number))
            return []

        async def post_review(self, owner, repo, pull_number, review_payload):
            review_calls.append((owner, repo, pull_number, review_payload))

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/demo")
    monkeypatch.setenv("PR_NUMBER", "12")
    monkeypatch.delenv("GEP_API_KEY", raising=False)
    monkeypatch.setattr(reviewer_main, "load_event", lambda: {
        "pull_request": {
            "number": 12,
            "head": {"sha": "current-head-sha"},
            "base": {"sha": "base-sha"},
        }
    })
    monkeypatch.setattr(reviewer_main, "GitHubMCPProvider", FakeProvider)
    install_fake_lifecycle(monkeypatch)
    monkeypatch.setattr(
        reviewer_main,
        "call_capgemini",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("GEP must not run for an empty PR diff")),
    )

    reviewer_main.main()

    assert read_calls == [("octo", "demo", 12)]
    assert len(review_calls) == 1
    owner, repo, pull_number, payload = review_calls[0]
    assert (owner, repo, pull_number) == ("octo", "demo", 12)
    assert payload["commit_id"] == "current-head-sha"
    assert payload["comments"] == []
    assert "## No reviewable code changes detected" in payload["body"]
    assert "no effective code changes to analyze" in payload["body"]
    assert "No AI model call was required" in payload["body"]
    assert "Human approval remains required." in payload["body"]
    assert "No findings" not in payload["body"]
    output = capsys.readouterr().out
    assert "[AI] Generative Engine call skipped" in output
    assert "[REVIEW] Preparing no-change advisory" in output


def test_lifecycle_publishes_inline_comment_only_for_new_finding():
    posted = []
    finalized = []

    class FakeProvider:
        async def post_review(self, owner, repo, pull_number, review_payload):
            posted.append(review_payload)

    class FakeLifecycle:
        async def reconcile(self, **kwargs):
            records = kwargs["current_findings"]
            return {
                "records": records,
                "new_findings": [records[1]],
                "existing_findings": [records[0]],
                "resolved_findings": [],
                "reopened_findings": [],
                "thread_ids_before": {"old-thread"},
                "state_comment_id": 99,
            }

        async def finalize(self, **kwargs):
            finalized.append(kwargs["comparison"])
            return kwargs["comparison"]

    result = ReviewResult.model_validate({
        "decision": "ADVISORY",
        "risk_level": "HIGH",
        "findings": [
            {
                "severity": "HIGH",
                "category": "Security",
                "file": "src/app.py",
                "line": 8,
                "title": "Existing validation",
                "issue": "Input is unchecked.",
                "recommendation": "Validate input.",
            },
            {
                "severity": "MEDIUM",
                "category": "Reliability",
                "file": "src/app.py",
                "line": 9,
                "title": "New pagination issue",
                "issue": "The result is not paginated.",
                "recommendation": "Add pagination.",
            },
            {
                "severity": "MEDIUM",
                "category": "Reliability",
                "file": "src/app.py",
                "line": 9,
                "title": "New pagination issue",
                "issue": "The result is not paginated.",
                "recommendation": "Add pagination.",
            },
        ],
        "summary": "Two findings.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security", "Reliability"],
    })
    line_map = {"src/app.py": [
        {"line": 8, "content": "if not owner_id: raise ValueError()"},
        {"line": 9, "content": "return items"},
    ]}
    payload = build_github_review_payload(result, line_map, "head-sha")

    reviewer_main.post_review_with_lifecycle(
        FakeProvider(),
        FakeLifecycle(),
        "octo",
        "demo",
        12,
        "head-sha",
        payload,
        review_result=result,
        changed_line_map=line_map,
        reviewed_files=["src/app.py"],
    )

    assert len(posted) == 1
    assert len(posted[0]["comments"]) == 1
    assert "New pagination issue" in posted[0]["comments"][0]["body"]
    assert "Existing validation" not in posted[0]["comments"][0]["body"]
    assert f"<!-- ai-review-finding-id:{finalized[0]['new_findings'][0]['finding_id']} -->" in posted[0]["comments"][0]["body"]
    assert "## Finding Lifecycle" in posted[0]["body"]
    assert len(finalized) == 1