import pytest

from reviewer import review
from reviewer.models import ReviewResult


def test_filter_changed_files_ignores_generated_and_locked_files():
    files = [
        {"filename": "src/app.py", "patch": "@@\n+print('ok')\n"},
        {"filename": "node_modules/pkg/index.js", "patch": "+unwanted"},
        {"filename": "dist/bundle.js", "patch": "+bundle"},
        {"filename": "package-lock.json", "patch": "+lock"},
        {"filename": "image.png", "patch": "binary"},
    ]

    filtered = review.filter_changed_files(files)
    assert filtered["files_reviewed"] == ["src/app.py"]
    assert "node_modules/pkg/index.js" in filtered["files_skipped"]
    assert "package-lock.json" in filtered["files_skipped"]


def test_filter_changed_files_truncates_large_diff():
    big_patch = "@@\n" + "+" + ("x" * 20000) + "\n"
    files = [{"filename": "src/big.py", "patch": big_patch}]

    filtered = review.filter_changed_files(files, max_file_diff_chars=100, max_review_input_chars=200)
    assert len(filtered["diff_text"]) <= 200 + 50
    assert "[truncated]" in filtered["diff_text"]


def test_policy_loading_reads_expected_policy_text():
    policy = review.load_policy()
    assert "UNTRUSTED DATA" in policy
    assert "Security" in policy
    assert "Human approval remains required" in policy


def test_format_review_markdown_contains_expected_sections():
    result = ReviewResult.model_validate(
        {
            "decision": "ADVISORY / CHANGES REQUIRED",
            "risk_level": "MODERATE RISK",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "category": "Coding Standards",
                    "file": "src/app.py",
                    "line": 79,
                    "title": "Keep naming consistent",
                    "issue": "The name is unclear.",
                    "recommendation": "Rename the variable.",
                    "suggested_fix": "value = parse_input(data)",
                }
            ],
            "summary": "One moderate issue was found.",
            "files_reviewed": ["src/app.py"],
            "files_skipped": [],
            "categories_reviewed": ["Coding Standards"],
        }
    )

    markdown = review.format_review_markdown(result)
    assert "# AI Code Review" in markdown
    assert "## Overall Assessment" in markdown
    assert "## Review Summary" in markdown
    assert "## Merge Recommendation" in markdown
    assert "Line 79" in markdown


def test_call_capgemini_rejects_malformed_json(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("bad json")

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(review.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="malformed JSON"):
        review.call_capgemini(
            policy_text="policy",
            user_prompt="prompt",
            api_key="abc",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
        )


def test_no_meaningful_files_returns_empty_diff():
    filtered = review.filter_changed_files([])
    assert filtered["files_reviewed"] == []
    assert filtered["diff_text"] == ""


def test_build_review_prompt_uses_untrusted_data_language():
    prompt = review.build_review_prompt(
        repo_slug="octo/demo",
        pr_title="Improve auth",
        pr_body="Fix login",
        diff_text="FILE: src/app.py\n+enabled = True",
    )

    assert "UNTRUSTED DATA" in prompt
    assert "Repository: octo/demo" in prompt
    assert "FILE: src/app.py" in prompt
